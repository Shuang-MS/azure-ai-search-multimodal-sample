from io import BytesIO
import base64
import os
import re
from typing import Dict
from azure.storage.blob.aio import BlobClient


async def get_blob_as_base64(blob_client: BlobClient):
    try:
        stream = BytesIO()
        download_stream = await blob_client.download_blob()
        await download_stream.readinto(stream)

        base64_image = base64.b64encode(stream.getvalue()).decode("utf-8")
        return base64_image

    except Exception as e:
        print(f"Error retrieving blob as Base64: {e}")
        return None


def build_blob_metadata_from_filename(file_name: str) -> Dict[str, str]:
    """Derive category/models metadata from filenames shaped as category+model1_model2."""
    metadata: Dict[str, str] = {}
    base_name = os.path.splitext(file_name)[0]
    category_part, models_part = (base_name.split("+", 1) + [""])[:2]

    category_value = category_part.strip()
    if category_value:
        metadata["category"] = category_value

    models_raw = models_part.strip()
    if models_raw:
        models = [model.strip() for model in models_raw.split("_") if model.strip()]
    else:
        models = []

    if models:
        metadata["models"] = ",".join(models)
        metadata["modelKeys"] = ",".join(_normalize_model_key(model) for model in models)

    return metadata


def _normalize_model_key(model: str) -> str:
    """Produce a normalized token that is safe for filtering/faceting."""
    return re.sub(r"[^a-z0-9]", "", model.lower())
