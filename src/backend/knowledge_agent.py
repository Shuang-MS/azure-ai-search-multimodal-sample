import asyncio
import json
import logging
import aiohttp
from typing import Any, Dict, List, Optional
from data_model import DataModel
from models import Message, GroundingResults, GroundingResult
from azure.search.documents.agent.aio import KnowledgeAgentRetrievalClient
from azure.search.documents.agent.models import KnowledgeAgentRetrievalResponse
from azure.search.documents.indexes.models import (
    KnowledgeAgent as AzureSearchKnowledgeAgent,
    KnowledgeAgentTargetIndex,
    KnowledgeAgentAzureOpenAIModel,
    AzureOpenAIVectorizerParameters,
)
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from grounding_retriever import GroundingRetriever

logger = logging.getLogger("grounding")


class KnowledgeAgentGrounding(GroundingRetriever):
    def __init__(
        self,
        retrieval_agent_client: KnowledgeAgentRetrievalClient,
        search_client: SearchClient,
        index_client: SearchIndexClient,
        data_model: DataModel,
        index_name: str,
        agent_name: str,
        azure_openai_endpoint: str,
        azure_openai_searchagent_deployment: str,
        azure_openai_searchagent_model: str,
    ):
        self.retrieval_agent_client = retrieval_agent_client
        self.search_client = search_client
        self.index_client = index_client
        self.data_model = data_model
        self.index_name = index_name

        self._create_retrieval_agent(
            agent_name,
            azure_openai_endpoint,
            azure_openai_searchagent_deployment,
            azure_openai_searchagent_model,
        )

    def _create_retrieval_agent(
        self,
        agent_name,
        azure_openai_endpoint,
        azure_openai_searchagent_deployment,
        azure_openai_searchagent_model,
    ):
        logger.info(f"Creating retrieval agent for {agent_name}")
        try:
            asyncio.create_task(
                self.index_client.create_or_update_agent(
                    agent=AzureSearchKnowledgeAgent(
                        name=agent_name,
                        target_indexes=[
                            KnowledgeAgentTargetIndex(
                                index_name=self.index_name,
                                default_include_reference_source_data=True,
                            )
                        ],
                        models=[
                            KnowledgeAgentAzureOpenAIModel(
                                azure_open_ai_parameters=AzureOpenAIVectorizerParameters(
                                    resource_url=azure_openai_endpoint,
                                    deployment_name=azure_openai_searchagent_deployment,
                                    model_name=azure_openai_searchagent_model,
                                )
                            )
                        ],
                    )
                )
            )
        except Exception as e:
            logger.error(f"Failed to create/update agent {agent_name}: {str(e)}")
            raise

    async def retrieve(
        self,
        user_message: str,
        chat_thread: List[Message],
        options: dict,
    ) -> GroundingResults:

        try:
            messages = [
                *chat_thread,
                {"role": "user", "content": [{"text": user_message, "type": "text"}]},
            ]

            result = await self.retrieval_agent_client.retrieve(
                retrieval_request={
                    "messages": messages,
                    "target_index_params": [
                        {
                            "indexName": self.index_name,
                            "includeReferenceSourceData": False,
                        }
                    ],
                },
            )

            result_dict = result.as_dict()
            references: List[GroundingResult] = []
            for ref in result_dict["response"]:
                for content in ref.get("content", []):
                    content_text = json.loads(content.get("text", "{}"))
                    for reference in content_text:
                        document_key = self._get_document_id(
                            reference["ref_id"], result
                        )
                        reference_copy = dict(reference)
                        references.append(
                            {
                                "ref_id": reference["ref_id"],
                                "doc_key": document_key,
                                "content": reference_copy,
                                "content_type": "text",  # Knowledge agent currently only returns text content
                            }
                        )
            return {
                "references": references,
                "search_queries": self._get_search_queries(result),
            }
        except aiohttp.ClientError as e:
            logger.error(f"Error calling Azure AI Search Retrieval Agent: {str(e)}")
            raise

    async def _get_text_citations(
        self, ref_ids: List[str], grounding_results: GroundingResults
    ) -> List[dict]:
        if not ref_ids:
            return []

        unique_ref_ids = list(dict.fromkeys(ref_ids))
        references_lookup: Dict[str, GroundingResult] = {
            reference["ref_id"]: reference for reference in grounding_results
        }
        fuzzy_matched_ids = []
        matched_references: List[Optional[GroundingResult]] = []

        for ref_id in unique_ref_ids:
            matched_id, reference = self._get_reference_with_fuzzy_id(
                ref_id, references_lookup
            )
            matched_references.append(reference)
            if matched_id and matched_id != ref_id:
                fuzzy_matched_ids.append((ref_id, matched_id))

        async def fetch_document(doc_id: str):
            try:
                return await self.search_client.get_document(doc_id)
            except Exception as exc:
                logger.warning(
                    "Failed to fetch document %s for citation lookup: %s",
                    doc_id,
                    exc,
                )
                return exc

        document_ids: List[str] = []
        fetch_tasks = []
        for ref_id, reference in zip(unique_ref_ids, matched_references):
            document_id = ref_id
            if reference:
                document_id = (
                    reference.get("doc_key")
                    or reference.get("content", {}).get("doc_key")
                    or ref_id
                )
            document_ids.append(document_id)
            fetch_tasks.append(fetch_document(document_id))
        documents = await asyncio.gather(*fetch_tasks)

        citations: List[dict] = []
        for ref_id, doc_id, document, reference in zip(
            unique_ref_ids, document_ids, documents, matched_references
        ):
            citation = None
            if not isinstance(document, Exception):
                try:
                    citation = self.data_model.extract_citation(document)
                except Exception as exc:
                    logger.warning(
                        "Unable to build citation from document %s (lookup id %s): %s",
                        ref_id,
                        doc_id,
                        exc,
                    )

            if citation is None:
                if reference:
                    citation = self._citation_from_reference(reference)

            if citation:
                citations.append(citation)

        if fuzzy_matched_ids:
            logger.info(
                "Resolved %d knowledge agent citation ids via fuzzy matching: %s",
                len(fuzzy_matched_ids),
                fuzzy_matched_ids,
            )

        return citations

    async def _get_image_citations(
        self, ref_ids: List[str], grounding_results: GroundingResults
    ) -> List[dict]:
        return []

    """Need to use document id as the reference id so I can lookkup the document properties for citations"""

    def _get_search_queries(self, response: KnowledgeAgentRetrievalResponse):
        queries = []
        for activity in response.activity:
            if activity.type != "AzureSearchQuery":
                continue
            activity_payload = activity.as_dict()
            queries.append(
                {
                    "query": activity_payload.get("query", ""),
                    "model": "",
                }
            )
        return queries

    def _get_document_id(
        self, ref_id: str, response: KnowledgeAgentRetrievalResponse
    ) -> str:
        for ref in response.references:
            ref_dict = ref.as_dict()
            if str(ref_dict["id"]) == str(ref_id):
                return ref_dict["doc_key"]
        raise ValueError(f"Reference ID {ref_id} not found in response")

    def _citation_from_reference(self, reference: GroundingResult) -> dict:
        """Best-effort fallback when we cannot rehydrate a document from the index."""
        content: Dict[str, Any] = reference.get("content", {}) or {}
        location = content.get("locationMetadata") or content.get("location_metadata") or {}

        return {
            "docId": content.get("docId")
            or content.get("document_id")
            or content.get("text_document_id")
            or reference.get("ref_id"),
            "content_id": reference.get("ref_id"),
            "title": content.get("title")
            or content.get("document_title")
            or "Knowledge Agent source",
            "text": content.get("text")
            or content.get("content")
            or "Citation content unavailable.",
            "locationMetadata": {
                "pageNumber": location.get("pageNumber")
                or location.get("page_number")
                or 0,
                "boundingPolygons": location.get("boundingPolygons")
                or location.get("bounding_polygons")
                or "",
            },
        }
