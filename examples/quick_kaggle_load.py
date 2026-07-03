#!/usr/bin/env python3
"""
Quick example: Download rplan-dataset2025 from Kaggle and load into RAG.

This is a simplified example that demonstrates basic kagglehub integration.
For more advanced usage, see scripts/download_kaggle_dataset.py

Setup:
    1. Install kagglehub: pip install kagglehub
    2. Authenticate: kagglehub auth login
    3. Run: python examples/quick_kaggle_load.py
"""

import kagglehub

def main():
    print("🔄 Downloading rplan-dataset2025 from Kaggle...")
    print("=" * 60)
    
    # Download latest version
    path = kagglehub.dataset_download("lkerkarabulut/rplan-dataset2025")
    print(f"\n✅ Path to dataset files: {path}")
    
    # List files
    import os
    print("\n📁 Dataset contents:")
    for root, dirs, files in os.walk(path):
        level = root.replace(path, '').count(os.sep)
        indent = ' ' * 2 * level
        print(f'{indent}{os.path.basename(root)}/')
        sub_indent = ' ' * 2 * (level + 1)
        for file in files[:10]:  # Show first 10 files
            print(f'{sub_indent}{file}')
        if len(files) > 10:
            print(f'{sub_indent}... and {len(files) - 10} more files')
    
    print("\n" + "=" * 60)
    print("📖 Next steps:")
    print("1. Run script: python scripts/download_kaggle_dataset.py")
    print("2. Start webapp: ./start_webapp.sh")
    print("3. Query through Chat interface with RAG enabled")

if __name__ == "__main__":
    main()
