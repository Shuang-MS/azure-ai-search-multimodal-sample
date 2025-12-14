import json
import logging
import re
from typing import List
from openai import AsyncOpenAI
from data_model import DataModel
from prompts import SEARCH_QUERY_SYSTEM_PROMPT
from models import Message, SearchConfig, GroundingResults, SearchQuery
from azure.search.documents.aio import SearchClient
from grounding_retriever import GroundingRetriever
import time

logger = logging.getLogger("groundingapi")


class SearchGroundingRetriever(GroundingRetriever):

    def __init__(
        self,
        search_client: SearchClient,
        openai_client: AsyncOpenAI,
        data_model: DataModel,
        chatcompletions_model_name: str,
    ):
        self.search_client = search_client
        self.openai_client = openai_client
        self.data_model = data_model
        self.chatcompletions_model_name = chatcompletions_model_name

    async def retrieve(
        self,
        user_message: str,
        chat_thread: List[Message],
        options: SearchConfig,
    ) -> GroundingResults:

        query_details = await self._generate_search_query(user_message, chat_thread)

        try:
            payload = self.data_model.create_search_payload(
                query_details["query"],
                options,
                query_details.get("model"),
            )

            start = time.perf_counter()
            search_results = await self.search_client.search(
                search_text=payload["search"],
                top=payload["top"],
                vector_queries=payload["vector_queries"],
                query_type=payload.get("query_type", "simple"),
                select=payload["select"],
                filter=payload.get("filter"),
            )
            logger.info(f"Azure AI Search request took {time.perf_counter() - start:.2f} seconds")
        except Exception as e:
            raise Exception(f"Azure AI Search request failed: {str(e)}")

        results_list = []
        async for result in search_results:
            results_list.append(result)

        references = await self.data_model.collect_grounding_results(results_list)

        return {
            "search_queries": [query_details],
            "references": references,
        }

    async def _generate_search_query(
        self, user_message: str, chat_thread: List[Message]
    ) -> SearchQuery:
        try:
            recent_user_messages = self._extract_recent_user_messages(chat_thread)

            prompt_parts = []
            if recent_user_messages:
                formatted_history = "\n".join(
                    f"- {message}" for message in recent_user_messages
                )
                prompt_parts.append(
                    "Earlier user messages for context:\n" + formatted_history
                )
            prompt_parts.append(f"Current user question:\n{user_message}")

            prompt_content = "\n\n".join(prompt_parts)
            start = time.perf_counter()
            response = await self.openai_client.chat.completions.create(
                model=self.chatcompletions_model_name,
                messages=[
                    {"role": "system", "content": SEARCH_QUERY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt_content},
                ],
            )

            logger.info(f"Search query generation took {time.perf_counter() - start:.2f} seconds")
            parsed_query = self._parse_query_response(response.choices[0].message.content, user_message)
            return parsed_query
        except Exception as e:
            raise Exception(
                f"Error while calling Azure OpenAI to generate a search query: {str(e)}"
            )

    def _parse_query_response(self, raw_response: str, fallback_query: str) -> SearchQuery:
        """Parse the LLM response into structured query/model fields."""
        query_value = ""
        model_value = ""

        if raw_response:
            cleaned_response = raw_response.strip()
            if cleaned_response.startswith("```") and cleaned_response.endswith("```"):
                # Handle fenced code block outputs by stripping the delimiters and optional language hints.
                cleaned_response = "\n".join(
                    line for line in cleaned_response.splitlines()[1:-1]
                ).strip()

            try:
                response_json = json.loads(cleaned_response)
                query_value = str(response_json.get("query", "")).strip()
                model_value = str(response_json.get("model", "")).strip()
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse search query response as JSON: %s",
                    cleaned_response,
                )
                query_value = cleaned_response

        if not query_value:
            logger.warning(
                "Search query generation returned empty query; falling back to user input."
            )
            query_value = fallback_query

        return {
            "query": query_value,
            "model": model_value or "",
        }

    def _extract_recent_user_messages(
        self, chat_thread: List[Message], max_messages: int = 3
    ) -> List[str]:
        """Return up to the last `max_messages` user messages as plain text."""
        user_messages: List[str] = []

        for message in chat_thread:
            if message.get("role") != "user":
                continue

            # Combine text content chunks, if any
            content_items = message.get("content", [])
            text_parts = [
                self._clean_text(item.get("text", ""))
                for item in content_items
                if isinstance(item, dict)
                and item.get("type") == "text"
                and item.get("text", "").strip()
            ]

            if text_parts:
                user_messages.append(" ".join(text_parts).strip())

        return user_messages[-max_messages:]

    _citation_pattern = re.compile(
        r"\[((?:[^\]]+_(?:text_sections|normalized_images)_\d+)|([a-z0-9]{12}_))\]"
    )

    def _clean_text(self, text: str) -> str:
        """Normalize text by removing citation markers and trimming whitespace."""
        if not text:
            return ""

        without_citations = self._citation_pattern.sub("", text)
        return " ".join(without_citations.split()).strip()

    async def _get_image_citations(
        self, ref_ids: List[str], grounding_results: GroundingResults
    ) -> List[dict]:
        return self._extract_citations(ref_ids, grounding_results)

    async def _get_text_citations(
        self, ref_ids: List[str], grounding_results: GroundingResults
    ) -> List[dict]:
        return self._extract_citations(ref_ids, grounding_results)

    def _extract_citations(
        self, ref_ids: List[str], grounding_results: GroundingResults
    ) -> List[dict]:
        if not ref_ids:
            return []

        references = {
            grounding_result["ref_id"]: grounding_result
            for grounding_result in grounding_results
        }
        extracted_citations = []
        missing_ref_ids = []
        fuzzy_matched_ids = []

        for ref_id in ref_ids:
            matched_id, reference = self._get_reference_with_fuzzy_id(
                ref_id, references
            )
            if reference:
                citation = self.data_model.extract_citation(reference)
                # When we fuzzy-match, force the canonical id from the grounding
                # results so downstream consumers (e.g. inline images) resolve
                # against the actual reference rather than the LLM's raw token.
                if matched_id:
                    citation["content_id"] = matched_id
                extracted_citations.append(citation)
                if matched_id and matched_id != ref_id:
                    fuzzy_matched_ids.append((ref_id, matched_id))
            else:
                missing_ref_ids.append(ref_id)

        if fuzzy_matched_ids:
            logger.info(
                "Resolved %d citation ids via fuzzy matching: %s",
                len(fuzzy_matched_ids),
                fuzzy_matched_ids,
            )

        if missing_ref_ids:
            logger.warning(
                "Dropping %d citation ids not found in grounding results: %s",
                len(missing_ref_ids),
                missing_ref_ids,
            )
        return extracted_citations
