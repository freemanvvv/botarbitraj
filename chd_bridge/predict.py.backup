from denoising_diffusion_pytorch import Unet, GaussianDiffusion, Trainer
import os
import sys
import pickle

import torch
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
if DEVICE == "cpu":
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Using device: {DEVICE}", file=sys.stderr, flush=True)


def predict_prepare():
    # patched for Mac: os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    results_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "predict_model")
    train_num_workers = 0
    with open(os.path.join(results_folder, "params.pkl"), "rb") as f:
        params = pickle.load(f)

    model = Unet(**params["unet_dict"])

    # params["diffusion_dict"]["sampling_timesteps"] = 50
    diffusion = GaussianDiffusion(model, **params["diffusion_dict"])

    trainer = Trainer(
        diffusion,
        "",
        "",
        "",
        **params["trainer_dict"],
        results_folder=results_folder,
        train_num_workers=train_num_workers,
        mode="predict",
        inject_step=40
    )
    # Load EMA weights (epoch 98)
    trainer.load(98)
    return trainer


if __name__ == "__main__":
    trainer = predict_prepare()
    print(f"⚡ Trainer ready on {trainer.device}")
