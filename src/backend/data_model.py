from abc import ABC, abstractmethod
from typing import List, Optional
from models import (
    SearchRequestParameters,
    SearchConfig,
    GroundingResult,
    GroundingResults,
)


class DataModel(ABC):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @abstractmethod
    def create_search_payload(
        self, query: str, search_config: SearchConfig, model: Optional[str] = None
    ) -> SearchRequestParameters:
        """Creates the search request payload."""
        pass

    @abstractmethod
    def extract_citation(
        self,
        document: dict,
    ) -> dict:
        """Extracts citations from search results."""
        pass

    @abstractmethod
    async def collect_grounding_results(self, search_results: List[dict]) -> list:
        """Collects and formats documents from search results."""
        pass


class DocumentPerChunkDataModel(DataModel):
    def create_search_payload(
        self, query: str, search_config: SearchConfig, model: Optional[str] = None
    ) -> SearchRequestParameters:
        """Creates the search request payload with vector/semantic/hybrid configurations using a configured vectorizer."""

        payload = {
            "search": query,
            "top": search_config["chunk_count"],
            "vector_queries": [
                {
                    "text": query,
                    "fields": "content_embedding",
                    "kind": "text",
                    "k": search_config["chunk_count"],
                }
            ],
            # Only request fields guaranteed to exist in the index to avoid $select errors.
            "select": "content_id, content_text, document_title, text_document_id, image_document_id, locationMetadata, content_path",
        }

        model_filter = self._build_model_filter(model)
        if model_filter:
            payload["filter"] = model_filter

        if search_config["use_semantic_ranker"]:
            payload["query_type"] = "semantic"

        return payload

    @staticmethod
    def _build_model_filter(model: Optional[str]) -> Optional[str]:
        if not model:
            return None

        sanitized = model.strip()
        if not sanitized:
            return None

        sanitized = sanitized.replace("'", "''")
        return f"search.ismatch('{sanitized}', 'document_title', 'full', 'any')"

    def extract_citation(self, document):
        return {
            "locationMetadata": document["locationMetadata"],
            "text": document["content_text"],
            "title": document["document_title"],
            "content_id": document["content_id"],
            "docId": (
                document["text_document_id"]
                if document["text_document_id"] is not None
                else document["image_document_id"]
            ),
        }

    async def collect_grounding_results(
        self, search_results: List[dict]
    ) -> List[GroundingResult]:
        collected_documents = []
        for result in search_results:
            is_image = result.get("image_document_id") is not None
            is_text = result.get("text_document_id") is not None

            if is_text and result["content_text"] is not None:
                collected_documents.append(
                    {
                        "ref_id": result["content_id"],
                        "content": {
                            "ref_id": result["content_id"],
                            "text": result["content_text"],
                        },
                        "content_type": "text",
                        **result,
                    }
                )
            elif is_image and result["content_path"] is not None:
                collected_documents.append(
                    {
                        "ref_id": result["content_id"],
                        "content": result["content_path"],
                        "content_type": "image",
                        **result,
                    }
                )
            else:
                raise ValueError(
                    f"Values for both image_chunk_document_id and text_chunk_document_id are missing for result: {result}"
                )
        return collected_documents
