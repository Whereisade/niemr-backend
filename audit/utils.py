from typing import Any, Dict
from django.forms.models import model_to_dict
from django.db.models import Model

def safe_model_dict(instance: Model, include=None, exclude=None) -> Dict[str, Any]:
    """
    Convert model to dict safely (skip big/file fields).
    """
    exclude = set(exclude or [])
    # naive skip list (extend as required)
    exclude.update({"password", "file", "image", "content", "sha256", "size_bytes"})
    data = model_to_dict(instance)
    # stringify FKs and values that aren't JSON-serializable
    for k, v in list(data.items()):
        if hasattr(v, "pk"):
            data[k] = v.pk
    return {k: v for k, v in data.items() if k not in exclude}
