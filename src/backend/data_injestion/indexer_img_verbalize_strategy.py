import asyncio
import glob
import os
from itertools import cycle

import aiofiles
from azure.search.documents.indexes.models import (
    AIServicesAccountIdentity,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    ComplexField,
    CustomAnalyzer,
    FieldMapping,
    HnswAlgorithmConfiguration,
    HnswParameters,
    IndexerExecutionStatus,
    IndexingParameters,
    IndexingParametersConfiguration,
    IndexProjectionMode,
    InputFieldMappingEntry,
    NativeBlobSoftDeleteDeletionDetectionPolicy,
    PatternTokenizer,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchIndexer,
    SearchIndexerDataContainer,
    SearchIndexerDataSourceConnection,
    SearchIndexerDataSourceType,
    SearchIndexerIndexProjection,
    SearchIndexerIndexProjectionSelector,
    SearchIndexerIndexProjectionsParameters,
    SearchIndexerKnowledgeStore,
    SearchIndexerKnowledgeStoreFileProjectionSelector,
    SearchIndexerKnowledgeStoreProjection,
    SearchIndexerSkillset,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    TokenFilterName,
    VectorSearch,
    VectorSearchProfile,
)
from data_injestion.models import ProcessRequest
from helpers import build_blob_metadata_from_filename
from data_injestion.skills import (
    getAzureOpenAIEmbeddingSkill,
    getAzureOpenAIEmbeddingSkillForVerbalizedImage,
    getChatCompletionSkill,
    getDocumentIntelligenceLayOutSkill,
    getShaperSkill,
    getBlobMetadataFanoutSkill,
)
from data_injestion.strategy import Strategy


class IndexerImgVerbalizationStrategy(Strategy):
    """
    A strategy for handling chat completion logic.
    """

    def _buildSkills(self, request: ProcessRequest):
        skills = [
            getDocumentIntelligenceLayOutSkill(),
            getBlobMetadataFanoutSkill(
                name="text-sections-blob-metadata",
                context_path="/document/text_sections/*",
            ),
            getBlobMetadataFanoutSkill(
                name="image-blob-metadata",
                context_path="/document/normalized_images/*",
            ),
            getChatCompletionSkill(
                uri=request.chatCompletionEndpoint,
                deploymentName=request.chatCompletionDeployment
            ),
            getAzureOpenAIEmbeddingSkill(
                deploymentId=request.aoaiEmbeddingDeployment,
                resourceUri=request.aoaiEmbeddingEndpoint,
                modelName=request.aoaiEmbeddingDeployment,
            ),
            getAzureOpenAIEmbeddingSkillForVerbalizedImage(
                deploymentId=request.aoaiEmbeddingDeployment,
                resourceUri=request.aoaiEmbeddingEndpoint,
                modelName=request.aoaiEmbeddingDeployment,
            ),
            getShaperSkill(request.knowledgeStoreContainer),
        ]

        skillSet = SearchIndexerSkillset(
            name=f"{request.indexName}-skillset",
            skills=skills,
            index_projection=SearchIndexerIndexProjection(
                selectors=[
                    SearchIndexerIndexProjectionSelector(
                        target_index_name=request.indexName,
                        source_context="/document/text_sections/*",
                        parent_key_field_name="text_document_id",
                        mappings=[
                            InputFieldMappingEntry(
                                name="content_embedding",
                                source="/document/text_sections/*/text_vector",
                            ),
                            InputFieldMappingEntry(
                                name="content_text",
                                source="/document/text_sections/*/content",
                            ),
                            InputFieldMappingEntry(
                                name="locationMetadata",
                                source="/document/text_sections/*/locationMetadata",
                            ),
                            InputFieldMappingEntry(
                                name="document_title", source="/document/document_title"
                            ),
                            InputFieldMappingEntry(
                                name="category",
                                source="/document/text_sections/*/blob_metadata/category"
                            ),
                            InputFieldMappingEntry(
                                name="models",
                                source="/document/text_sections/*/blob_metadata/models"
                            )
                        ],
                    ),
                    SearchIndexerIndexProjectionSelector(
                        target_index_name=request.indexName,
                        source_context="/document/normalized_images/*",
                        parent_key_field_name="image_document_id",
                        mappings=[
                            InputFieldMappingEntry(
                                name="content_embedding",
                                source="/document/normalized_images/*/verbalizedImage_vector",
                            ),
                            InputFieldMappingEntry(
                                name="content_text",
                                source="/document/normalized_images/*/verbalizedImage",
                            ),
                            InputFieldMappingEntry(
                                name="content_path",
                                source="/document/normalized_images/*/new_normalized_images/imagePath",
                            ),
                            InputFieldMappingEntry(
                                name="locationMetadata",
                                source="/document/normalized_images/*/locationMetadata",
                            ),
                            InputFieldMappingEntry(
                                name="document_title", source="/document/document_title"
                            )
                        ],
                    ),
                ],
                parameters=SearchIndexerIndexProjectionsParameters(
                    projection_mode=IndexProjectionMode.SKIP_INDEXING_PARENT_DOCUMENTS
                ),
            ),
            cognitive_services_account=AIServicesAccountIdentity(
                subdomain_url=request.cognitiveServicesEndpoint,
            ),
            knowledge_store=SearchIndexerKnowledgeStore(
                storage_connection_string=f"ResourceId=/subscriptions/{request.subscriptionId}/resourceGroups/{request.resourceGroup}/providers/Microsoft.Storage/storageAccounts/{request.blobServiceClient.account_name};",
                projections=[
                    SearchIndexerKnowledgeStoreProjection(
                        files=[
                            SearchIndexerKnowledgeStoreFileProjectionSelector(
                                storage_container=request.knowledgeStoreContainer,
                                source="/document/normalized_images/*",
                            )
                        ]
                    )
                ],
            ),
        )
        return skillSet

    async def _buildDataSource(self, request: ProcessRequest):
        container_client = request.blobServiceClient.get_container_client(
            request.blobSource
        )
        try:
            await container_client.create_container()
        except Exception as e:
            print(f"Error creating container: {e}")

        document_paths = glob.glob(os.path.join(request.localDataSource, "*.*"))
        print(f"Document paths: {document_paths}")
        for doc_path in document_paths:
            print(f"Uploading file: {doc_path}")
            async with aiofiles.open(doc_path, "rb") as f:
                file_bytes = await f.read()
                file_name = os.path.basename(doc_path)
                blob_metadata = build_blob_metadata_from_filename(file_name)
                upload_kwargs = {"metadata": blob_metadata} if blob_metadata else {}
                await container_client.upload_blob(
                    file_name, file_bytes, overwrite=True, **upload_kwargs
                )

        ds_container = SearchIndexerDataContainer(name=request.blobSource)
        data_source_connection = SearchIndexerDataSourceConnection(
            name=f"{request.indexName}-blob",
            type=SearchIndexerDataSourceType.AZURE_BLOB,
            connection_string=f"ResourceId=/subscriptions/{request.subscriptionId}/resourceGroups/{request.resourceGroup}/providers/Microsoft.Storage/storageAccounts/{request.blobServiceClient.account_name};",
            container=ds_container,
            data_deletion_detection_policy=NativeBlobSoftDeleteDeletionDetectionPolicy(),
        )

        return data_source_connection

    def _buildIndex(self, request: ProcessRequest):
        document_title_tokenizer = PatternTokenizer(
            name="documentTitleTokenizer",
            pattern=r"(?:\s+|_)",
        )
        document_title_analyzer = CustomAnalyzer(
            name="documentTitleAnalyzer",
            tokenizer_name=document_title_tokenizer.name,
            token_filters=[TokenFilterName.LOWERCASE]
        )
        fields = [
            SearchableField(
                name="content_id",
                type=SearchFieldDataType.String,
                key=True,
                analyzer_name="keyword",
            ),
            SimpleField(
                name="text_document_id",
                type=SearchFieldDataType.String,
                searchable=False,
                filterable=True,
                hidden=False,
                sortable=False,
                facetable=False,
            ),
            SimpleField(
                name="image_document_id",
                type=SearchFieldDataType.String,
                searchable=False,
                filterable=True,
                hidden=False,
                sortable=False,
                facetable=False,
            ),
            SearchableField(
                name="document_title",
                type=SearchFieldDataType.String,
                searchable=True,
                filterable=True,
                hidden=False,
                sortable=True,
                facetable=True,
                analyzer_name=document_title_analyzer.name,
            ),
            SearchableField(
                name="content_text",
                type=SearchFieldDataType.String,
                searchable=True,
                filterable=True,
                hidden=False,
                sortable=True,
                facetable=True,
            ),
            SearchField(
                name="content_embedding",
                hidden=False,
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                vector_search_dimensions=3072,
                searchable=True,
                vector_search_profile_name=f"{request.indexName}-profile",
            ),
            SimpleField(
                name="content_path",
                type=SearchFieldDataType.String,
                searchable=False,
                filterable=True,
                hidden=False,
                sortable=False,
                facetable=False,
            ),
            SimpleField(
                name="category",
                type=SearchFieldDataType.String,
                searchable=False,
                filterable=True,
                hidden=False,
                sortable=False,
                facetable=True,
            ),
            SearchField(
                name="models",
                type=SearchFieldDataType.String,
                searchable=False,
                filterable=True,
                hidden=False,
                sortable=False,
                facetable=True,
            ),
            SearchField(
                name="modelKeys",
                type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                searchable=False,
                filterable=True,
                hidden=False,
                sortable=False,
                facetable=False,
            ),
            ComplexField(
                name="locationMetadata",
                fields=[
                    SimpleField(
                        name="pageNumber",
                        type=SearchFieldDataType.Int32,
                        searchable=False,
                        filterable=True,
                        hidden=False,
                        sortable=True,
                        facetable=True,
                    ),
                    SimpleField(
                        name="boundingPolygons",
                        type=SearchFieldDataType.String,
                        searchable=False,
                        hidden=False,
                        filterable=False,
                        sortable=False,
                        facetable=False,
                    ),
                ],
            ),
        ]
        index = SearchIndex(
            fields=fields,
            name=request.indexName,
            analyzers=[document_title_analyzer],
            tokenizers=[document_title_tokenizer],
            vector_search=VectorSearch(
                algorithms=[
                    HnswAlgorithmConfiguration(
                        name=f"{request.indexName}-algo",
                        parameters=HnswParameters(
                            metric="cosine",
                        ),
                    )
                ],
                vectorizers=[
                    AzureOpenAIVectorizer(
                        vectorizer_name=f"{request.indexName}-vectorizer",
                        parameters=AzureOpenAIVectorizerParameters(
                            resource_url=request.aoaiEmbeddingEndpoint,
                            deployment_name=request.aoaiEmbeddingDeployment,
                            model_name=request.aoaiEmbeddingModel,
                        ),
                    )
                ],
                profiles=[
                    VectorSearchProfile(
                        algorithm_configuration_name=f"{request.indexName}-algo",
                        vectorizer_name=f"{request.indexName}-vectorizer",
                        name=f"{request.indexName}-profile",
                    )
                ],
            ),
            semantic_search=SemanticSearch(
                default_configuration_name="semanticconfig",
                configurations=[
                    SemanticConfiguration(
                        name="semanticconfig",
                        prioritized_fields=SemanticPrioritizedFields(
                            title_field=SemanticField(
                                field_name="document_title",
                            ),
                            content_fields=[
                                SemanticField(
                                    field_name="content_text",
                                )
                            ],
                        ),
                    )
                ]
            ),
        )

        return index

    async def run(self, request: ProcessRequest, **kwargs):
        """
        Executes the image verbalization strategy logic.
        """
        print("Executing indexer chatCompletionStrategy", kwargs)

        index_client = request.indexClient
        indexer_client = request.indexerClient

        await index_client.create_or_update_index(self._buildIndex(request))
        try:
            await indexer_client.create_or_update_data_source_connection(
                await self._buildDataSource(request),
            )
        except Exception as e:
            print(f"Error updating data source connection: {e}")

        await indexer_client.create_or_update_skillset(self._buildSkills(request))

        search_indexer = await indexer_client.create_or_update_indexer(
            indexer=SearchIndexer(
                name=f"{request.indexName}-indexer",
                data_source_name=f"{request.indexName}-blob",
                target_index_name=request.indexName,
                skillset_name=f"{request.indexName}-skillset",
                parameters=IndexingParameters(
                    batch_size=1,
                    configuration=IndexingParametersConfiguration(
                        data_to_extract="contentAndMetadata",
                        allow_skillset_to_read_file_data=True,
                        query_timeout=None,
                    ),
                ),
                field_mappings=[
                    FieldMapping(
                        source_field_name="metadata_storage_name",
                        target_field_name="document_title",
                    )
                ],
            )
        )

        await indexer_client.run_indexer(search_indexer.name)

        while True:
            indexer_status = await indexer_client.get_indexer_status(
                search_indexer.name
            )
            if indexer_status and indexer_status.status != "running":
                print(
                    f"\r Indexer status: {indexer_status.status}",
                    end="",
                    flush=True,
                )
                break
            if (
                indexer_status
                and indexer_status.last_result
                and indexer_status.last_result.status
                != IndexerExecutionStatus.IN_PROGRESS
            ):
                print(
                    f"\r Indexer last result status: {indexer_status.last_result.status}",
                    end="",
                    flush=True,
                )
                break

            spinner = cycle(["|", "/", "-", "\\"])
            for _ in range(10):
                print(
                    f"\rIndexer execution is in progress, please wait... {next(spinner)}",
                    end="",
                    flush=True,
                )
                await asyncio.sleep(0.5)
