"""
This example follows the experimental settings of the Cat Cookie and Cat Doudo experiments in the ICLR 2024 paper,
"Differentially Private Synthetic Data via Foundation Model APIs 1: Images" (https://arxiv.org/abs/2305.15560).

For detailed information about parameters and APIs, please consult the documentation of the Private Evolution library:
https://microsoft.github.io/DPSDA/.
"""

import pandas as pd
import os
import sys
import numpy as np

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '../..'))
sys.path.append(root_dir)

from pe.data.image import Cat
from pe.logging import setup_logging
from pe.runner import PE
from pe.population import PEPopulation
# from pe.api.image import StableDiffusion
from pe.api.image import ImprovedDiffusion270M
from pe.embedding.image import Inception
from pe.histogram import NearestNeighbors
from pe.callback import SaveCheckpoints
from pe.callback import SampleImages
from pe.callback import SaveAllImages
from pe.callback import ComputeFID
from pe.logger import ImageFile
from pe.logger import CSVPrint
from pe.logger import LogPrint


pd.options.mode.copy_on_write = True


if __name__ == "__main__":
    exp_folder = "results/image/cat"

    setup_logging(log_file=os.path.join(exp_folder, "log.txt"))

    data = Cat(root_dir="/tmp/data/")
    # api = StableDiffusion(
    #     prompt={"cookie": "A photo of ragdoll cat", "doudou": "A photo of ragdoll cat"},
    #     variation_degrees=list(np.arange(1.0, 0.9, -0.02)) + list(np.arange(0.88, 0.36, -0.04)),
    # )

    api = ImprovedDiffusion270M(
        variation_degrees=list(range(0, 42, 2)),
        timestep_respacing="100",
        model_path=os.path.expanduser("~/.torch/datasets/imagenet64_cond_270M_250K.pt")
    )

    embedding = Inception(res=512, batch_size=100)
    histogram = NearestNeighbors(
        embedding=embedding,
        mode="L2",
        lookahead_degree=8,
        api=api,
    )
    population = PEPopulation(api=api, histogram_threshold=2)

    save_checkpoints = SaveCheckpoints(os.path.join(exp_folder, "checkpoint"))
    sample_images = SampleImages()
    save_all_images = SaveAllImages(output_folder=os.path.join(exp_folder, "all_images"))
    compute_fid = ComputeFID(priv_data=data, embedding=embedding)

    image_file = ImageFile(output_folder=exp_folder)
    csv_print = CSVPrint(output_folder=exp_folder)
    log_print = LogPrint()

    pe_runner = PE(
        priv_data=data,
        population=population,
        histogram=histogram,
        callbacks=[save_checkpoints, sample_images, compute_fid, save_all_images],
        loggers=[image_file, csv_print, log_print],
    )
    pe_runner.run(
        num_samples_schedule=[200] * 18,
        delta=1e-3,
        noise_multiplier=2,
        checkpoint_path=os.path.join(exp_folder, "checkpoint"),
    )
