"""JSON endpoint behind the editor's image upload button."""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_POST

from apps.core.editor.uploads import UploadRejectedError

from .editor_uploads import (
    QuotaExceededError,
    UploadNotAllowedError,
    store_editor_image,
)

logger = logging.getLogger(__name__)


def _error(message: str, status: int, code: str) -> JsonResponse:
    return JsonResponse({"error": message, "code": code}, status=status)


@require_POST
def upload(request: HttpRequest) -> JsonResponse:
    """Accept one image for an editor context and return its public URL."""
    user = request.user
    if not isinstance(user, get_user_model()) or not user.is_authenticated:
        return _error("Sign in to upload images.", 401, "auth")
    context = str(request.POST.get("context", ""))[:40]
    files = request.FILES.getlist("file")
    if len(files) != 1:
        return _error("Send exactly one image.", 400, "invalid")
    try:
        image = store_editor_image(user, context, files[0])
    except UploadNotAllowedError as error:
        return _error(str(error), 403, "forbidden")
    except QuotaExceededError as error:
        return _error(str(error), 429, "quota")
    except UploadRejectedError as error:
        return _error(str(error), 400, error.code)
    return JsonResponse(
        {
            "url": image.file.url,
            "width": image.width,
            "height": image.height,
            "id": image.pk,
        },
        status=201,
    )
