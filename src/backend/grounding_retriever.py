from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple
from models import Message, GroundingResults


class GroundingRetriever(ABC):
    """Abstract base class for answer grounding functionality.

    This class defines the contract for classes that implement answer grounding,
    which involves retrieving relevant documents based on user messages and chat history.
    """

    @abstractmethod
    async def retrieve(
        self,
        user_message: str,
        chat_thread: List[Message],
        options: dict,
    ) -> GroundingResults:
        """Retrieve relevant documents based on the user message and chat history.

        Args:
            user_message: The current user message to process
            chat_thread: The history of messages in the current chat
            options: Configuration options for the retriever

        Returns:
            GroundingResults containing the retrieved references and search queries

        Raises:
            Exception: If the search request fails or document processing fails
        """
        pass

    @abstractmethod
    async def _get_text_citations(
        self, ref_ids: List[str], grounding_results: GroundingResults
    ) -> List[dict]:
        pass

    @abstractmethod
    async def _get_image_citations(
        self, ref_ids: List[str], grounding_results: GroundingResults
    ) -> List[dict]:
        pass

    @staticmethod
    def _levenshtein_distance(source: str, target: str) -> int:
        """Compute a simple Levenshtein edit distance between two strings."""
        if source == target:
            return 0

        if len(source) < len(target):
            source, target = target, source

        previous_row = list(range(len(target) + 1))
        for i, source_char in enumerate(source, start=1):
            current_row = [i]
            for j, target_char in enumerate(target, start=1):
                insertions = previous_row[j] + 1
                deletions = current_row[j - 1] + 1
                substitutions = previous_row[j - 1] + (source_char != target_char)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    @classmethod
    def _get_reference_with_fuzzy_id(
        cls,
        requested_id: str,
        references: Dict[str, dict],
        max_distance: int = 10,
    ) -> Tuple[Optional[str], Optional[dict]]:
        """Return the best matching reference allowing a small edit distance."""
        if not requested_id:
            return None, None

        exact_match = references.get(requested_id)
        if exact_match:
            return requested_id, exact_match

        normalized_id = str(requested_id).lower()
        best_match_key: Optional[str] = None
        best_match_ref: Optional[dict] = None
        best_distance: Optional[int] = None

        for candidate_key, candidate_ref in references.items():
            distance = cls._levenshtein_distance(
                normalized_id, str(candidate_key).lower()
            )
            if distance / len(normalized_id) * 100 > max_distance:
                continue

            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_match_key = candidate_key
                best_match_ref = candidate_ref

                if best_distance == 0:
                    break

        return best_match_key, best_match_ref
