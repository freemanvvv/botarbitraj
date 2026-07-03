"""
Download and integrate kagglehub datasets into Construction AI Copilot RAG.

Usage:
    python scripts/download_kaggle_dataset.py --dataset "lkerkarabulut/rplan-dataset2025" --output data/datasets
"""
import os
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import NORMATIVES_DIR, OUTPUT_DIR

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def download_kaggle_dataset(dataset_identifier: str, output_dir: str = None):
    """
    Download dataset from Kaggle using kagglehub.
    
    Args:
        dataset_identifier (str): Dataset identifier like "username/dataset-name"
        output_dir (str): Optional output directory. Default: data/datasets
        
    Returns:
        str: Path to downloaded dataset
    """
    try:
        import kagglehub
    except ImportError:
        logger.error("kagglehub not installed. Run: pip install kagglehub")
        return None
    
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "datasets")
    
    os.makedirs(output_dir, exist_ok=True)
    
    logger.info(f"Downloading dataset: {dataset_identifier}")
    logger.info(f"Output directory: {output_dir}")
    
    try:
        # Download latest version
        path = kagglehub.dataset_download(dataset_identifier, path=output_dir)
        logger.info(f"✅ Dataset downloaded successfully")
        logger.info(f"Path to dataset files: {path}")
        return path
    except Exception as e:
        logger.error(f"❌ Failed to download dataset: {e}")
        return None


def integrate_dataset_into_rag(dataset_path: str, normalize_text: bool = True):
    """
    Integrate downloaded dataset files into RAG pipeline.
    
    Args:
        dataset_path (str): Path to downloaded dataset
        normalize_text (bool): Whether to normalize text before indexing
        
    Returns:
        bool: True if successful
    """
    try:
        from src.rag_pipeline import get_rag
        import pdfplumber
        from pathlib import Path
    except ImportError as e:
        logger.error(f"Import error: {e}")
        return False
    
    logger.info(f"Integrating dataset from: {dataset_path}")
    
    # Find all text files, PDFs, and markdown files
    dataset_path = Path(dataset_path)
    files_to_process = []
    
    for ext in ['*.pdf', '*.txt', '*.md', '*.markdown']:
        files_to_process.extend(dataset_path.glob(f'**/{ext}'))
    
    if not files_to_process:
        logger.warning(f"No supported files found in {dataset_path}")
        return False
    
    logger.info(f"Found {len(files_to_process)} files to process")
    
    try:
        rag = get_rag("simple")
        processed_count = 0
        
        for file_path in files_to_process:
            try:
                logger.info(f"Processing: {file_path.name}")
                
                # Read file content
                if file_path.suffix == '.pdf':
                    # Extract text from PDF
                    text_content = ""
                    with pdfplumber.open(str(file_path)) as pdf:
                        for page in pdf.pages:
                            text_content += page.extract_text() or ""
                elif file_path.suffix in ['.txt', '.md', '.markdown']:
                    # Read text/markdown file
                    with open(file_path, 'r', encoding='utf-8') as f:
                        text_content = f.read()
                else:
                    continue
                
                if normalize_text:
                    text_content = normalize_dataset_text(text_content)
                
                # Add to RAG with metadata
                metadata = {
                    "source": f"kaggle_{dataset_path.name}",
                    "file": file_path.name,
                    "date_added": datetime.now().isoformat(),
                    "file_type": file_path.suffix
                }
                
                # Add document to RAG
                doc_id = f"kaggle_{file_path.stem}_{int(datetime.now().timestamp())}"
                rag.add_document(
                    content=text_content,
                    metadata=metadata,
                    doc_id=doc_id
                )
                
                logger.info(f"✅ Added: {file_path.name}")
                processed_count += 1
                
            except Exception as e:
                logger.error(f"Error processing {file_path.name}: {e}")
                continue
        
        logger.info(f"✅ Integration complete. Processed {processed_count}/{len(files_to_process)} files")
        return processed_count > 0
        
    except Exception as e:
        logger.error(f"RAG integration failed: {e}")
        return False


def normalize_dataset_text(text: str) -> str:
    """
    Normalize text from dataset for better RAG indexing.
    
    Args:
        text (str): Raw text from dataset
        
    Returns:
        str: Normalized text
    """
    import re
    
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    
    # Remove special characters but keep punctuation
    text = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]', '', text)
    
    # Trim
    text = text.strip()
    
    return text


def list_available_datasets():
    """
    Print some popular Construction/Architecture datasets from Kaggle.
    """
    popular_datasets = [
        ("lkerkarabulut/rplan-dataset2025", "Floor plan dataset for residential buildings"),
        ("devanshkhandelwal/construction-site-images", "Construction site images"),
        ("jkuler/lol-buildings", "Building architecture dataset"),
        ("titir2/architectural-heritage-elements", "Architectural heritage elements"),
    ]
    
    print("\n📊 Popular Construction/Architecture Datasets on Kaggle:")
    print("-" * 70)
    for dataset_id, description in popular_datasets:
        print(f"• {dataset_id}")
        print(f"  {description}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Download and integrate Kaggle datasets into Construction AI Copilot"
    )
    
    parser.add_argument(
        "--dataset",
        type=str,
        default="lkerkarabulut/rplan-dataset2025",
        help="Kaggle dataset identifier (e.g., 'username/dataset-name')"
    )
    
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory (default: data/datasets)"
    )
    
    parser.add_argument(
        "--integrate-rag",
        action="store_true",
        default=True,
        help="Integrate downloaded files into RAG pipeline"
    )
    
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="Skip text normalization"
    )
    
    parser.add_argument(
        "--list",
        action="store_true",
        help="List popular datasets"
    )
    
    args = parser.parse_args()
    
    if args.list:
        list_available_datasets()
        return
    
    # Download dataset
    dataset_path = download_kaggle_dataset(args.dataset, args.output)
    
    if dataset_path and args.integrate_rag:
        # Integrate into RAG
        success = integrate_dataset_into_rag(
            dataset_path,
            normalize_text=not args.no_normalize
        )
        
        if success:
            logger.info("\n🎉 Dataset successfully integrated into RAG!")
            logger.info(f"You can now query this dataset through the chat interface.")
            logger.info(f"Run: ./start_webapp.sh")
    elif not dataset_path:
        logger.error("Failed to download dataset")
        sys.exit(1)


if __name__ == "__main__":
    main()
