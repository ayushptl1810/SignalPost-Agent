from __future__ import annotations

import re
from typing import Any, Protocol


DECISION_LABELS = (
    "exact_entity",
    "related_entity",
    "wrong_entity",
    "insufficient_evidence",
)


class CandidateClassifier(Protocol):
    def classify(self, profile: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        ...


def _candidate_text(candidate: dict[str, Any]) -> str:
    fields = [
        "title",
        "snippet",
        "url",
        "page_title",
        "page_text",
        "identity_text",
    ]
    return " ".join(str(candidate.get(field) or "") for field in fields).casefold()


def _probabilities(choice: str, confidence: float) -> dict[str, float]:
    confidence = max(0.0, min(1.0, float(confidence)))
    remainder = (1.0 - confidence) / (len(DECISION_LABELS) - 1)
    return {
        label: confidence if label == choice else remainder
        for label in DECISION_LABELS
    }


class RuleBasedCandidateClassifier:
    """Cheap, deterministic typed-decision baseline used before Laya is calibrated."""

    classifier_name = "rules_v1"

    def classify(self, profile: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        # Never include the target profile in the evidence text. Otherwise the
        # target's own name/number would make every candidate look exact.
        text = _candidate_text(candidate)
        organisation_number = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
        name = " ".join(str(profile.get("name") or "").casefold().split())
        related_markers = (
            "group", "gruppen", "konsern", "portfolio", "parent", "subsidiary",
            "holding company", "datterselskap", "eier", "managed by",
        )
        if organisation_number and organisation_number in re.sub(r"\D", "", text):
            choice, confidence = "exact_entity", 0.96
        elif name and name in text:
            choice, confidence = "exact_entity", 0.76
        elif any(marker in text for marker in related_markers):
            choice, confidence = "related_entity", 0.78
        else:
            choice, confidence = "insufficient_evidence", 0.72
        return {
            "classifier": self.classifier_name,
            "choice": choice,
            "confidence": confidence,
            "probabilities": _probabilities(choice, confidence),
        }


class LayaCandidateClassifier:
    """Optional local Laya typed-decision classifier for candidate triage."""

    classifier_name = "laya_typed_decision_v1"

    def __init__(self, router: Any | None = None, *, model: str = "multilingual") -> None:
        if router is None:
            try:
                from laya import Router
            except ImportError as exc:
                raise RuntimeError(
                    "Laya is not installed. Install the optional classifier dependency "
                    "before using --classifier laya."
                ) from exc
            router = Router()
        self.router = router
        self.model = model

    def classify(self, profile: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        state = (
            "Target legal entity:\n"
            f"Name: {profile.get('name', '')}\n"
            f"Organisation number: {profile.get('organisation_number', '')}\n"
            f"Municipality: {profile.get('municipality', '')}\n\n"
            "Candidate external result:\n"
            f"URL: {candidate.get('url', '')}\n"
            f"Title: {candidate.get('title', candidate.get('page_title', ''))}\n"
            f"Snippet: {candidate.get('snippet', '')}\n"
            f"Page text: {str(candidate.get('page_text', ''))[:8000]}"
        )
        questions = {
            "relationship": {
                "type": "choice",
                "instructions": "Classify the candidate's relationship to the target legal entity.",
                "criteria": {
                    "exact_entity": "The page represents the exact target legal entity.",
                    "related_entity": "The page represents a parent, group, subsidiary, branch, brand, or managed profile.",
                    "wrong_entity": "The page represents a different or unrelated entity.",
                    "insufficient_evidence": "The evidence is not enough to decide.",
                },
            }
        }
        result = self.router.predict(state, questions, model=self.model)
        answer = ((result or {}).get("answers") or {}).get("relationship") or {}
        choice = answer.get("choice")
        if choice not in DECISION_LABELS:
            choice = "insufficient_evidence"
        probabilities = answer.get("probabilities") or _probabilities(
            choice,
            float(answer.get("confidence") or 0.0),
        )
        normalized_probabilities = {
            label: float(probabilities.get(label, 0.0))
            for label in DECISION_LABELS
        }
        confidence = float(answer.get("confidence") or normalized_probabilities.get(choice, 0.0))
        return {
            "classifier": self.classifier_name,
            "model": self.model,
            "choice": choice,
            "confidence": confidence,
            "probabilities": normalized_probabilities,
            "routing": (result or {}).get("routing"),
        }


def build_candidate_classifier(backend: str = "rules", **kwargs: Any) -> CandidateClassifier:
    if backend == "rules":
        return RuleBasedCandidateClassifier()
    if backend == "laya":
        return LayaCandidateClassifier(**kwargs)
    raise ValueError(f"Unknown candidate classifier backend: {backend}")
