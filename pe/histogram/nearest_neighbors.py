import numpy as np
import os
from collections import Counter
import copy

from pe.histogram import Histogram
from pe.constant.data import CLEAN_HISTOGRAM_COLUMN_NAME
from pe.constant.data import LOOKAHEAD_EMBEDDING_COLUMN_NAME
from pe.constant.data import LABEL_ID_COLUMN_NAME
from pe.constant.data import HISTOGRAM_NEAREST_NEIGHBORS_VOTING_IDS_COLUMN_NAME
from pe.logging import execution_logger


class NearestNeighbors(Histogram):
    """Compute the nearest neighbors histogram. Each private sample will vote for their closest `num_nearest_neighbors`
    synthetic samples to construct the histogram. The l2 norm of the votes from each private sample is normalized to 1.
    """

    def __init__(
        self,
        embedding,
        mode,
        lookahead_degree,
        lookahead_log_folder=None,
        voting_details_log_folder=None,
        api=None,
        num_nearest_neighbors=1,
        backend="auto",
    ):
        """Constructor.

        :param embedding: The :py:class:`pe.embedding.embedding.Embedding` object to compute the embedding of samples
        :type embedding: :py:class:`pe.embedding.embedding.Embedding`
        :param mode: The distance metric to use for finding the nearest neighbors. It should be one of the following:
            "l2" (l2 distance), "cos_sim" (cosine similarity), "ip" (inner product). Not all backends support all
            modes
        :type mode: str
        :param lookahead_degree: The degree of lookahead to compute the embedding of synthetic samples. If it is 0, the
            original embedding is used. If it is greater than 0, the embedding of the synthetic samples is computed by
            averaging the embeddings of the synthetic samples generated by the variation API for `lookahead_degree`
            times
        :type lookahead_degree: int
        :param lookahead_log_folder: The folder to save the logs of the lookahead. If it is None, the logs are not
            saved. Defaults to None
        :type lookahead_log_folder: str, optional
        :param voting_details_log_folder: The folder to save the logs of the voting details. If it is None, the logs
            are not saved. Defaults to None
        :type voting_details_log_folder: str, optional
        :param api: The API to generate synthetic samples. It should be provided when `lookahead_degree` is greater
            than 0. Defaults to None
        :type api: :py:class:`pe.api.api.API`, optional
        :param num_nearest_neighbors: The number of nearest neighbors to consider for each private sample, defaults to
            1
        :type num_nearest_neighbors: int, optional
        :param backend: The backend to use for finding the nearest neighbors. It should be one of the following:
            "faiss" (FAISS), "sklearn" (scikit-learn), "auto" (using FAISS if available, otherwise scikit-learn).
            Defaults to "auto". FAISS supports GPU and is much faster when the number of synthetic samples and/or
            private samples is large. It requires the installation of `faiss-gpu` or `faiss-cpu` package. See
            https://faiss.ai/
        :type backend: str, optional
        :raises ValueError: If the `api` is not provided when `lookahead_degree` is greater than 0
        :raises ValueError: If the `backend` is unknown
        """
        super().__init__()
        self._embedding = embedding
        self._mode = mode
        self._lookahead_degree = lookahead_degree
        self._lookahead_log_folder = lookahead_log_folder
        self._voting_details_log_folder = voting_details_log_folder
        self._api = api
        self._num_nearest_neighbors = num_nearest_neighbors
        if self._lookahead_degree > 0 and self._api is None:
            raise ValueError("API should be provided when lookahead_degree is greater than 0")
        if backend.lower() == "faiss":
            from pe.histogram.nearest_neighbor_backend.faiss import search

            self._search = search
        elif backend.lower() == "sklearn":
            from pe.histogram.nearest_neighbor_backend.sklearn import search

            self._search = search
        elif backend.lower() == "auto":
            from pe.histogram.nearest_neighbor_backend.auto import search

            self._search = search
        else:
            raise ValueError(f"Unknown backend: {backend}")

    def _log_lookahead(self, syn_data, lookahead_id):
        """Log the lookahead data.

        :param syn_data: The lookahead data
        :type syn_data: :py:class:`pe.data.data.Data`
        :param lookahead_id: The ID of the lookahead
        :type lookahead_id: int
        """
        if self._lookahead_log_folder is None:
            return
        labels = set(list(syn_data.data_frame[LABEL_ID_COLUMN_NAME].values))
        assert len(labels) == 1
        label = list(labels)[0]
        iteration = syn_data.metadata["iteration"]
        log_folder = os.path.join(
            self._lookahead_log_folder, f"{iteration}", f"label-id{label}_lookahead{lookahead_id}"
        )
        syn_data.save_checkpoint(log_folder)

    def _log_voting_details(self, priv_data, syn_data, ids):
        """Log the voting details.

        :param priv_data: The private data
        :type priv_data: :py:class:`pe.data.data.Data`
        :param syn_data: The synthetic data
        :type syn_data: :py:class:`pe.data.data.Data`
        :param ids: The IDs of the nearest neighbors for each private sample
        :type ids: np.ndarray
        """
        if self._voting_details_log_folder is None:
            return
        labels = set(list(priv_data.data_frame[LABEL_ID_COLUMN_NAME].values))
        assert len(labels) == 1
        label = list(labels)[0]
        iteration = syn_data.metadata["iteration"]
        log_folder = os.path.join(self._voting_details_log_folder, f"{iteration}", f"label-id{label}")
        priv_data = copy.deepcopy(priv_data)
        priv_data.data_frame[HISTOGRAM_NEAREST_NEIGHBORS_VOTING_IDS_COLUMN_NAME] = list(ids)
        priv_data.save_checkpoint(log_folder)

    def _compute_lookahead_embedding(self, syn_data):
        """Compute the embedding of synthetic samples with lookahead.

        :param syn_data: The synthetic data
        :type syn_data: :py:class:`pe.data.data.Data`
        :return: The synthetic data with the computed embedding in the column
            :py:const:`pe.constant.data.LOOKAHEAD_EMBEDDING_COLUMN_NAME`
        :rtype: :py:class:`pe.data.data.Data`
        """
        if self._lookahead_degree == 0:
            syn_data = self._embedding.compute_embedding(syn_data)
            syn_data.data_frame[LOOKAHEAD_EMBEDDING_COLUMN_NAME] = syn_data.data_frame[self._embedding.column_name]
        else:
            embedding_list = []
            for lookahead_id in range(self._lookahead_degree):
                variation_data = self._api.variation_api(syn_data=syn_data)
                variation_data = self._embedding.compute_embedding(variation_data)
                self._log_lookahead(syn_data=variation_data, lookahead_id=lookahead_id)
                embedding_list.append(
                    np.stack(
                        variation_data.data_frame[self._embedding.column_name].values,
                        axis=0,
                    )
                )
            embedding = np.mean(embedding_list, axis=0)
            syn_data.data_frame[LOOKAHEAD_EMBEDDING_COLUMN_NAME] = list(embedding)
        self._log_lookahead(syn_data=syn_data, lookahead_id=-1)

        return syn_data

    def compute_histogram(self, priv_data, syn_data):
        """Compute the nearest neighbors histogram.

        :param priv_data: The private data
        :type priv_data: :py:class:`pe.data.data.Data`
        :param syn_data: The synthetic data
        :type syn_data: :py:class:`pe.data.data.Data`
        :return: The private data, possibly with the additional embedding column, and the synthetic data, with the
            computed histogram in the column :py:const:`pe.constant.data.CLEAN_HISTOGRAM_COLUMN_NAME` and possibly with
            the additional embedding column
        :rtype: tuple[:py:class:`pe.data.data.Data`, :py:class:`pe.data.data.Data`]
        """
        execution_logger.info(
            f"Histogram: computing nearest neighbors histogram for {len(priv_data.data_frame)} private "
            f"samples and {len(syn_data.data_frame)} synthetic samples"
        )

        priv_data = self._embedding.compute_embedding(priv_data)
        syn_data = self._compute_lookahead_embedding(syn_data)

        priv_embedding = np.stack(priv_data.data_frame[self._embedding.column_name].values, axis=0).astype(np.float32)
        syn_embedding = np.stack(syn_data.data_frame[LOOKAHEAD_EMBEDDING_COLUMN_NAME].values, axis=0).astype(
            np.float32
        )

        _, ids = self._search(
            syn_embedding=syn_embedding,
            priv_embedding=priv_embedding,
            num_nearest_neighbors=self._num_nearest_neighbors,
            mode=self._mode,
        )
        self._log_voting_details(priv_data=priv_data, syn_data=syn_data, ids=ids)

        counter = Counter(list(ids.flatten()))
        count = np.zeros(shape=syn_embedding.shape[0], dtype=np.float32)
        count[list(counter.keys())] = list(counter.values())
        count /= np.sqrt(self._num_nearest_neighbors)

        syn_data.data_frame[CLEAN_HISTOGRAM_COLUMN_NAME] = count

        execution_logger.info(
            f"Histogram: finished computing nearest neighbors histogram for {len(priv_data.data_frame)} private "
            f"samples and {len(syn_data.data_frame)} synthetic samples"
        )

        return priv_data, syn_data
