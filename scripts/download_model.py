"""
scripts/download_model.py

Run this ONCE on a machine with internet access to download the embedding
model into the project's data/models/ folder.  Then copy the whole project
(including data/models/) to the offline laptop and set EMBEDDING_MODEL in
.env to the local path.

Usage:
    python scripts/download_model.py
"""

import sys
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

HF_MODEL_ID = "intfloat/multilingual-e5-base"
DEST_RELATIVE = "data/models/multilingual-e5-base"


def main() -> None:
    from sentence_transformers import SentenceTransformer

    project_root = Path(__file__).parent.parent
    dest = project_root / DEST_RELATIVE

    if dest.exists():
        print(f"Model already exists at: {dest}")
        print("Delete the folder and re-run if you want to re-download.")
        return

    print(f"Downloading '{HF_MODEL_ID}' from HuggingFace...")
    print("(Requires internet access — approx. 280 MB)")
    model = SentenceTransformer(HF_MODEL_ID)
    dest.mkdir(parents=True, exist_ok=True)
    model.save(str(dest))

    print()
    print(f"Saved to: {dest}")
    print()
    print("Next steps on the offline machine:")
    print(f"  1. Copy this project folder (including data/models/) to the laptop.")
    print(f"  2. Add this line to .env :")
    print(f"       EMBEDDING_MODEL={DEST_RELATIVE}")
    print(f"  3. Run the pipeline normally:")
    print(f"       python pipeline.py embed")


if __name__ == "__main__":
    main()
