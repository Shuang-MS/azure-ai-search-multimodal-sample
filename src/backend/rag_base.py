import logging
import json
import time
import re
from typing import Dict, List, Tuple, Optional
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from aiohttp import web
import instructor
from openai import AsyncOpenAI
from grounding_retriever import GroundingRetriever
from models import (
    AnswerFormat,
    SearchConfig,
    GroundingResult,
    GroundingResults,
)
from processing_step import ProcessingStep

logger = logging.getLogger("rag")


class MessageType(Enum):
    ANSWER = "answer"
    CITATION = "citation"
    LOG = "log"
    ERROR = "error"
    END = "[END]"
    ProcessingStep = "processing_step"
    INFO = "info"


class RagBase(ABC):
    _CITATION_PATTERN = re.compile(
        r"\[((?:[^\]]+_(?:text_sections|normalized_images)_\d+)|(?:[A-Za-z0-9]{12}(?:_[^\]]+)?))\]"
    )
    _STREAM_DELIMITERS = [".\n", ", ", ". ", "; ", "\n", "[", "]"]

    def __init__(
        self,
        openai_client: AsyncOpenAI,
        chatcompletions_model_name: str,
    ):
        self.openai_client = openai_client
        self.chatcompletions_model_name = chatcompletions_model_name

    async def _handle_request(self, request: web.Request):
        request_params = await request.json()
        search_text = request_params.get("query", "")
        chat_thread = request_params.get("chatThread", [])
        config_dict = request_params.get("config", {})
        search_config = SearchConfig(
            chunk_count=config_dict.get("chunk_count", 10),
            openai_api_mode=config_dict.get("openai_api_mode", "chat_completions"),
            use_semantic_ranker=config_dict.get("use_semantic_ranker", False),
            use_streaming=config_dict.get("use_streaming", False),
            use_knowledge_agent=config_dict.get("use_knowledge_agent", False),
        )
        request_id = request_params.get("request_id", str(int(time.time())))
        response = await self._create_stream_response(request)
        try:
            await self._process_request(
                request_id, response, search_text, chat_thread, search_config
            )
        except Exception as e:
            print(e)
            logger.error(f"Error processing request: {str(e)}")
            await self._send_error_message(request_id, response, str(e))

        await self._send_end(response)
        return response

    @abstractmethod
    async def _process_request(
        self,
        request_id: str,
        response: web.StreamResponse,
        search_text: str,
        chat_thread: list,
        search_config: SearchConfig,
    ):
        pass

    async def _formulate_response(
        self,
        request_id: str,
        response: web.StreamResponse,
        messages: list,
        grounding_retriever: GroundingRetriever,
        grounding_results: GroundingResults,
        search_config: SearchConfig,
    ):
        """Handles streaming chat completion and sends citations."""

        logger.info("Formulating LLM response")
        await self._send_processing_step_message(
            request_id,
            response,
            ProcessingStep(title="LLM Payload", type="code", content=messages),
        )

        complete_response: dict = {}

        if search_config.get("use_streaming", False):
            logger.info("Streaming chat completion")
            chat_stream_response = instructor.from_openai(
                self.openai_client,
            ).chat.completions.create_partial(
                stream=True,
                model=self.chatcompletions_model_name,
                response_model=AnswerFormat,
                messages=messages,
                temperature=0.0,
                seed=42,
            )
            msg_id = str(uuid.uuid4())
            previous_answer = ""
            emitted_answer = ""
            line_buffer = ""

            async for stream_response in chat_stream_response:
                if stream_response.answer is not None:
                    current_answer = stream_response.answer or ""
                    new_chunk = current_answer[len(previous_answer) :]
                    previous_answer = current_answer

                    if not new_chunk:
                        continue

                    line_buffer += new_chunk

                    # Emit updates when we hit punctuation/newline boundaries,
                    # so the UI receives text in natural segments.
                    while True:
                        boundary = self._find_stream_boundary(line_buffer)
                        if boundary is None:
                            break

                        boundary_index, delimiter = boundary
                        end_index = boundary_index + len(delimiter)
                        completed_segment = line_buffer[:end_index]
                        line_buffer = line_buffer[end_index:]
                        emitted_answer += completed_segment
                        await self._send_answer_message(
                            request_id,
                            response,
                            msg_id,
                            emitted_answer,
                        )
                    complete_response = stream_response.model_dump()

            # Flush any remaining partial line once the stream ends so nothing is lost.
            if line_buffer:
                emitted_answer += line_buffer
                await self._send_answer_message(
                    request_id,
                    response,
                    msg_id,
                    emitted_answer,
                )
            if len(complete_response.keys()) == 0:
                raise ValueError("No response received from chat completion stream.")

        else:
            logger.info("Waiting for chat completion")
            chat_completion = await instructor.from_openai(
                self.openai_client,
            ).chat.completions.create(
                stream=False,
                model=self.chatcompletions_model_name,
                response_model=AnswerFormat,
                messages=messages,
            )
            msg_id = str(uuid.uuid4())

            if chat_completion is not None:
                await self._send_answer_message(
                    request_id, response, msg_id, chat_completion.answer
                )
                complete_response = chat_completion.model_dump()
            else:
                raise ValueError("No response received from chat completion stream.")

        answer_text = complete_response.get("answer", "")
        text_citation_ids = complete_response.get("text_citations") or []
        image_citation_ids = complete_response.get("image_citations") or []

        extracted_citation_ids = self._extract_citation_ids_from_answer(answer_text)
        (
            extracted_text_ids,
            extracted_image_ids,
            citation_aliases,
        ) = self._split_citations_by_type(
            extracted_citation_ids, grounding_results["references"]
        )

        canonical_extracted_text_ids = [
            citation_aliases.get(citation_id, citation_id)
            for citation_id in extracted_text_ids
        ]
        canonical_extracted_image_ids = [
            citation_aliases.get(citation_id, citation_id)
            for citation_id in extracted_image_ids
        ]

        text_citation_ids = self._merge_citation_lists(
            canonical_extracted_text_ids,
            [
                citation_aliases.get(citation_id, citation_id)
                for citation_id in text_citation_ids
            ],
        )
        image_citation_ids = self._merge_citation_lists(
            canonical_extracted_image_ids,
            [
                citation_aliases.get(citation_id, citation_id)
                for citation_id in image_citation_ids
            ],
        )

        complete_response["text_citations"] = text_citation_ids
        complete_response["image_citations"] = image_citation_ids

        await self._send_processing_step_message(
            request_id,
            response,
            ProcessingStep(title="LLM response", type="code", content=complete_response),
        )

        await self._extract_and_send_citations(
            request_id,
            response,
            grounding_retriever,
            grounding_results["references"],
            text_citation_ids,
            image_citation_ids,
            citation_aliases,
        )

    async def _extract_and_send_citations(
        self,
        request_id: str,
        response: web.StreamResponse,
        grounding_retriever: GroundingRetriever,
        grounding_results: List[GroundingResult],
        text_citation_ids: list,
        image_citation_ids: list,
        citation_aliases: Optional[Dict[str, str]],
    ):
        """Extracts and sends citations from search results."""
        citations = await self.extract_citations(
            grounding_retriever,
            grounding_results,
            text_citation_ids,
            image_citation_ids,
            citation_aliases,
        )

        await self._send_citation_message(
            request_id,
            response,
            request_id,
            citations.get("text_citations", []),
            citations.get("image_citations", []),
        )

    def _extract_citation_ids_from_answer(self, answer: str) -> List[str]:
        if not answer:
            return []

        matches = self._CITATION_PATTERN.findall(answer)
        citations = []
        seen = set()
        for citation_id in matches:
            if citation_id and citation_id not in seen:
                citations.append(citation_id)
                seen.add(citation_id)
        return citations

    def _split_citations_by_type(
        self,
        citation_ids: List[str],
        references: List[GroundingResult],
    ) -> Tuple[List[str], List[str], Dict[str, str]]:
        if not citation_ids:
            return [], [], {}

        reference_lookup: Dict[str, GroundingResult] = {
            str(ref["ref_id"]): ref for ref in references or [] if ref.get("ref_id")
        }
        text_ids: List[str] = []
        image_ids: List[str] = []
        text_seen = set()
        image_seen = set()
        alias_lookup: Dict[str, str] = {}

        for citation_id in citation_ids:
            if not citation_id:
                continue

            alias_lookup.setdefault(citation_id, citation_id)

            matched_id, reference = GroundingRetriever._get_reference_with_fuzzy_id(
                citation_id, reference_lookup
            )
            if matched_id:
                alias_lookup[citation_id] = matched_id
            content_type = (reference or {}).get("content_type")

            if not content_type:
                normalized_id = str(citation_id).lower()
                if "normalized_images" in normalized_id:
                    # Heuristic fallback for cases where the ref id drifts (e.g. due to casing),
                    # which still uniquely identifies image chunks.
                    content_type = "image"

            if content_type == "image":
                if citation_id not in image_seen:
                    image_ids.append(citation_id)
                    image_seen.add(citation_id)
            else:
                if citation_id not in text_seen:
                    text_ids.append(citation_id)
                    text_seen.add(citation_id)

        return text_ids, image_ids, alias_lookup

    @staticmethod
    def _merge_citation_lists(primary: List[str], secondary: List[str]) -> List[str]:
        merged: List[str] = []
        seen = set()

        for citation_id in primary + secondary:
            if citation_id and citation_id not in seen:
                merged.append(citation_id)
                seen.add(citation_id)

        return merged

    @classmethod
    def _find_stream_boundary(
        cls, buffer: str
    ) -> Optional[Tuple[int, str]]:
        if not buffer:
            return None

        boundary_index: Optional[int] = None
        boundary_delimiter: Optional[str] = None

        for delimiter in cls._STREAM_DELIMITERS:
            idx = buffer.find(delimiter)
            if idx == -1:
                continue

            if (
                boundary_index is None
                or idx < boundary_index
                or (idx == boundary_index and len(delimiter) > len(boundary_delimiter or ""))
            ):
                boundary_index = idx
                boundary_delimiter = delimiter

                if boundary_index == 0:
                    break

        if boundary_index is None or boundary_delimiter is None:
            return None

        return boundary_index, boundary_delimiter

    @abstractmethod
    async def extract_citations(
        self,
        grounding_retriever: GroundingRetriever,
        grounding_results: List[GroundingResult],
        text_citation_ids: list,
        image_citation_ids: list,
        citation_aliases: Optional[Dict[str, str]] = None,
    ) -> dict:
        pass

    async def _create_stream_response(self, request):
        """Creates and prepares the SSE stream response."""
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Connection": "keep-alive",
                "Cache-Control": "no-cache, no-transform",
            },
        )
        await response.prepare(request)
        return response

    async def _send_error_message(
        self, request_id: str, response: web.StreamResponse, message: str
    ):
        """Sends an error message through the stream."""
        await self._send_message(
            response,
            MessageType.ERROR.value,
            {
                "request_id": request_id,
                "message_id": str(uuid.uuid4()),
                "message": message,
            },
        )

    async def _send_info_message(
        self,
        request_id: str,
        response: web.StreamResponse,
        message: str,
        details: str = None,
    ):
        """Sends an info message through the stream."""
        await self._send_message(
            response,
            MessageType.INFO.value,
            {
                "request_id": request_id,
                "message_id": str(uuid.uuid4()),
                "message": message,
                "details": details,
            },
        )

    async def _send_processing_step_message(
        self,
        request_id: str,
        response: web.StreamResponse,
        processing_step: ProcessingStep,
    ):
        logger.info(
            f"Sending processing step message for step: {processing_step.title}"
        )
        await self._send_message(
            response,
            MessageType.ProcessingStep.value,
            {
                "request_id": request_id,
                "message_id": str(uuid.uuid4()),
                "processingStep": processing_step.to_dict(),
            },
        )

    async def _send_answer_message(
        self,
        request_id: str,
        response: web.StreamResponse,
        message_id: str,
        content: str,
    ):
        await self._send_message(
            response,
            MessageType.ANSWER.value,
            {
                "request_id": request_id,
                "message_id": message_id,
                "role": "assistant",
                "answerPartial": {"answer": content},
            },
        )

    async def _send_citation_message(
        self,
        request_id: str,
        response: web.StreamResponse,
        message_id: str,
        text_citations: list,
        image_citations: list,
    ):

        await self._send_message(
            response,
            MessageType.CITATION.value,
            {
                "request_id": request_id,
                "message_id": message_id,
                "textCitations": text_citations,
                "imageCitations": image_citations,
            },
        )

    async def _send_message(self, response, event, data):
        try:
            await response.write(
                f"event:{event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
            )
        except ConnectionResetError:
            # TODO: Something is wrong here, the messages attempted and failed here is not what the UI sees, thats another set of stream...
            # logger.warning("Connection reset by client.")
            pass
        except Exception as e:
            logger.error(f"Error sending message: {e}")

    async def _send_end(self, response):
        await self._send_message(response, MessageType.END.value, {})

    def attach_to_app(self, app, path):
        """Attaches the handler to the web app."""
        app.router.add_post(path, self._handle_request)
