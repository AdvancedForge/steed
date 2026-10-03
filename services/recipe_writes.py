"""Trusted payload previews and single-dispatch reviewed recipe writes."""

import hashlib
import json
import re

from services.connector_recipes import RecipeError, select
from services.connectors import ConnectorOutcome, ConnectorReview
from services.recipe_http import check_cancelled


def operation_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise RecipeError("Use the receipt's trusted operation ID")
    return value


class RecipeWrites:
    def __init__(self, connector):
        self.connector = connector

    def preview(self, tool, request):
        owner = self.connector
        if (
            tool.effect not in {"write", "send"}
            or request.connector != owner.account.connector
            or request.tool != tool.name
            or request.effect != tool.effect
            or request.artifact is None
        ):
            raise RecipeError("Write request differs from the reviewed recipe")
        destination = tool.destination(request.destination)
        operation = operation_id(request.operation_id)
        artifact = request.artifact
        if (
            hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
            != artifact.sha256
        ):
            raise RecipeError("Draft content changed")
        sources = {
            "artifact.title": artifact.title,
            "artifact.content": artifact.content,
            "destination": destination.removeprefix("email:")
            if tool.destination_type == "email"
            else destination,
            "operation_id": operation,
        }
        payload = json.dumps(
            tool.request_template.materialize(sources)
            if tool.request_template
            else {k: sources[v] for k, v in tool.request},
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        context = json.dumps(
            {
                "effect": tool.effect,
                "method": "POST",
                "url": owner.recipe.origin
                + tool.path.replace("{destination}", destination),
                "destination": destination,
                "account": owner.account.label,
                "account_id": owner.account.account_id,
                "subject": owner.account.subject,
                "source_sha256": owner.recipe.sha256,
                "artifact_sha256": artifact.sha256,
                "operation_id": operation,
                "operation_header": tool.idempotency_header or None,
                "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                **dict(tool.plan_context),
            },
            ensure_ascii=False,
            indent=2,
        )
        return ConnectorReview(payload, artifact.content, context).validate()

    def prepare(self, tool, request, cancelled):
        preview = self.preview(tool, request)
        self.connector.authorize(cancelled)
        return preview

    def execute(self, tool, request, cancelled):
        owner = self.connector
        try:
            preview = self.preview(tool, request)
            if request.review != preview:
                raise RecipeError("Approve this exact payload first")
            token, credentials = owner.authorize(cancelled)
            headers = {
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            }
            if tool.idempotency_header:
                headers[tool.idempotency_header] = request.operation_id
            url = owner.recipe.origin + tool.path.replace(
                "{destination}", request.destination
            )
            check_cancelled(cancelled)
            owner.catalog.active(owner.recipe)
            owner.store.active(owner.account)
        except Exception:
            return ConnectorOutcome(
                "not_applied",
                text="The payload was not dispatched; review, account or permission changed.",
            )
        try:
            # No retries, including token errors. Once HTTP entry begins the service
            # may have committed, regardless of the response or cancellation.
            response = owner.http.request(
                "POST",
                url,
                headers=headers,
                body=preview.before.encode("utf-8"),
                cancelled=cancelled,
            )
            check_cancelled(cancelled)
            owner.catalog.active(owner.recipe)
            owner.store.active(owner.account)
            return self.outcome(tool, response, credentials)
        except Exception:
            return ConnectorOutcome(
                "uncertain",
                text="The service may have applied this action. Check its receipt; do not resend automatically.",
            )

    def outcome(self, tool, response, credentials):
        from services.recipe_connector import redact

        selected = {label: select(response, path) for label, path in tool.response}
        return ConnectorOutcome(
            text=json.dumps(
                redact(selected, credentials), ensure_ascii=False, allow_nan=False
            )
        ).validate()

    def reconcile(self, tool, probe, cancelled):
        try:
            owner = self.connector
            tool.destination(probe.destination)
            operation = operation_id(probe.operation_id)
            token, credentials = owner.authorize(cancelled)
            rule = tool.reconciliation
            if rule is None:
                raise RecipeError("No reviewed reconciliation endpoint")
            response = owner.http.request(
                "GET",
                owner.recipe.origin + rule.path.replace("{operation_id}", operation),
                headers={"Authorization": "Bearer " + token},
                cancelled=cancelled,
            )
            check_cancelled(cancelled)
            owner.catalog.active(owner.recipe)
            owner.store.active(owner.account)
            if (
                select(response, rule.operation_id) != operation
                or select(response, rule.status) != rule.success
            ):
                raise RecipeError("Service has not confirmed this operation")
            return self.outcome(tool, response, credentials)
        except Exception:
            # Absence/errors cannot prove that an external service did not commit.
            return ConnectorOutcome(
                "uncertain",
                text="The service has not confirmed this exact operation. No write was retried.",
            )
