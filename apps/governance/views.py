import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware
from django.views.decorators.http import require_GET, require_POST

from .models import ReviewCase
from .services import (
    GovernanceError,
    decide_case,
    fulfill_case,
    report_issue,
    start_review,
)

logger = logging.getLogger(__name__)


def _payload(request):
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _error_response(exc):
    if isinstance(exc, PermissionDenied):
        return JsonResponse({"error": str(exc)}, status=403)
    if isinstance(exc, GovernanceError):
        return JsonResponse({"error": str(exc)}, status=409)
    if isinstance(exc, ValidationError):
        return JsonResponse({"error": exc.messages}, status=400)
    raise exc


def _case_json(case):
    return {
        "id": case.pk,
        "content_type": f"{case.content_type.app_label}.{case.content_type.model}",
        "object_id": case.object_id,
        "status": case.status,
        "revision": case.revision,
        "requested_by_id": case.requested_by_id,
        "reviewer_id": case.reviewer_id,
        "publisher_id": case.publisher_id,
        "created_at": case.created_at.isoformat(),
    }


@login_required
@require_GET
def case_list(request):
    if not (
        request.user.has_perm("governance.can_review_content")
        or request.user.has_perm("governance.can_publish_content")
    ):
        return JsonResponse({"error": "forbidden"}, status=403)
    cases = (
        ReviewCase.objects.filter(is_open=True)
        .select_related("content_type", "requested_by", "reviewer")
        .order_by("created_at")[:100]
    )
    return JsonResponse({"items": [_case_json(case) for case in cases]})


@login_required
@require_POST
def review_case(request, case_id):
    data = _payload(request)
    if data is None:
        return JsonResponse({"error": "invalid_json_object"}, status=400)
    get_object_or_404(ReviewCase, pk=case_id)
    try:
        case = start_review(
            case_id,
            request.user,
            rationale=data.get("rationale", ""),
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
    except (PermissionDenied, GovernanceError, ValidationError) as exc:
        return _error_response(exc)
    logger.info("Governance review started: case=%s actor=%s", case.pk, request.user.pk)
    return JsonResponse(_case_json(case))


@login_required
@require_POST
def decide_case_view(request, case_id):
    data = _payload(request)
    if data is None:
        return JsonResponse({"error": "invalid_json_object"}, status=400)
    get_object_or_404(ReviewCase, pk=case_id)
    try:
        case = decide_case(
            case_id,
            request.user,
            data.get("decision", ""),
            data.get("rationale", ""),
            decision_data=data.get("decision_data"),
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
    except (PermissionDenied, GovernanceError, ValidationError) as exc:
        return _error_response(exc)
    logger.info(
        "Governance decision recorded: case=%s status=%s actor=%s",
        case.pk,
        case.status,
        request.user.pk,
    )
    return JsonResponse(_case_json(case))


@login_required
@require_POST
def fulfill_case_view(request, case_id):
    data = _payload(request)
    if data is None:
        return JsonResponse({"error": "invalid_json_object"}, status=400)
    scheduled_at = None
    if data.get("scheduled_at"):
        scheduled_at = parse_datetime(data["scheduled_at"])
        if scheduled_at is None or not is_aware(scheduled_at):
            return JsonResponse(
                {"error": "scheduled_at_must_be_timezone_aware"}, status=400
            )
    get_object_or_404(ReviewCase, pk=case_id)
    try:
        case = fulfill_case(
            case_id,
            request.user,
            scheduled_at=scheduled_at,
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
    except (PermissionDenied, GovernanceError, ValidationError) as exc:
        return _error_response(exc)
    logger.info("Governance case fulfilled: case=%s actor=%s", case.pk, request.user.pk)
    return JsonResponse(_case_json(case))


@login_required
@require_POST
def report_issue_view(request, case_id):
    data = _payload(request)
    if data is None:
        return JsonResponse({"error": "invalid_json_object"}, status=400)
    get_object_or_404(ReviewCase, pk=case_id)
    try:
        case = report_issue(
            case_id,
            request.user,
            data.get("rationale", ""),
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
    except (PermissionDenied, GovernanceError, ValidationError) as exc:
        return _error_response(exc)
    logger.info(
        "Governance corrective action opened: case=%s actor=%s",
        case.pk,
        request.user.pk,
    )
    return JsonResponse(_case_json(case))
