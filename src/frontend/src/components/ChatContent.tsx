import React, { useEffect, useRef } from "react";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Button, Tooltip } from "@fluentui/react-components";

import { Citation, ProcessingStepsMessage, RoleType, Thread, ThreadType } from "../api/models";
import "./ChatContent.css";
import Citations from "./Citations";
import ProcessingSteps from "./ProcessingSteps";
import { Copy20Regular, BrainCircuit20Regular } from "@fluentui/react-icons";

const getMessageKey = (message: Thread) => message.message_id || `${message.request_id}:${message.type}:${message.role}`;
const messageHasCitations = (message: Thread) =>
    (message.textCitations?.length ?? 0) > 0 || (message.imageCitations?.length ?? 0) > 0;

interface Props {
    processingStepMsg: Record<string, ProcessingStepsMessage[]>;
    thread: Thread[];
}

const ChatContent: React.FC<Props> = ({ thread, processingStepMsg }) => {
    const [showProcessingSteps, setShowProcessingSteps] = React.useState(false);
    const [processRequestId, setProcessRequestId] = React.useState("");
    const [highlightedCitation, setHighlightedCitation] = React.useState<string | undefined>();
    const [showCopied, setShowCopied] = React.useState(false);

    const chatContainerRef = useRef<HTMLDivElement>(null);
    const loggedMessageIdsRef = useRef<Set<string>>(new Set<string>());
    const messageToBeCopied: Record<string, string> = {};

    useEffect(() => {
        if (chatContainerRef.current) {
            chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
        }
    }, [thread]);

    const buildCitationMap = (citations: Citation[]) => {
        const entries: [string, Citation][] = [];
        citations.forEach(citation => {
            const aliasSet = new Set<string>();
            if (citation.content_id) {
                aliasSet.add(citation.content_id);
            }
            (citation.contentAliases || []).forEach(alias => {
                if (alias) {
                    aliasSet.add(alias);
                }
            });
            aliasSet.forEach(alias => {
                entries.push([alias, citation]);
            });
        });
        return new Map(entries);
    };

    const messagesGroupedByRequestId = Object.values(
        thread.reduce((acc: { [key: string]: Thread[] }, message: Thread) => {
            if (!acc[message.request_id]) {
                acc[message.request_id] = [];
            }
            acc[message.request_id].push(message);
            return acc;
        }, {})
    );

    useEffect(() => {
        thread.forEach(message => {
            const messageKey = getMessageKey(message);
            if (!loggedMessageIdsRef.current.has(messageKey)) {
                console.info("Chat message", {
                    requestId: message.request_id,
                    type: message.type,
                    textCitationsCount: message.textCitations?.length ?? 0,
                    imageCitationsCount: message.imageCitations?.length ?? 0,
                    hasAnswer: Boolean(message.answerPartial?.answer),
                    hasCitations: messageHasCitations(message)
                });
                loggedMessageIdsRef.current.add(messageKey);
            }
        });
    }, [thread]);

    const citationRegex = /\[((?:[^\]]+_(?:text_sections|normalized_images)_\d+)|(?:[A-Za-z0-9]{12}(?:_[^\]]+)?))\]/g;

    const citationHit = (key: string, docId: string) => (
        <sup
            key={key}
            onMouseLeave={() => setHighlightedCitation(undefined)}
            onMouseEnter={() => setHighlightedCitation(docId)}
            className="citation-icon"
        >
            ◆
        </sup>
    );

    const inlineImage = (key: string, citation: Citation) => (
        <div><span
            key={key}
            className="inline-image-citation"
            onMouseEnter={() => setHighlightedCitation(citation.content_id)}
            onMouseLeave={() => setHighlightedCitation(undefined)}
        >
            {citation.imageDataUrl ? (
                <img src={citation.imageDataUrl} alt={citation.title || citation.docId || citation.content_id} />
            ) : (
                <span className="inline-image-missing">Image unavailable</span>
            )}
        </span></div>
    );

    const renderWithCitations = (
        children: React.ReactNode,
        textCitationMap: Map<string, Citation>,
        imageCitationMap: Map<string, Citation>
    ) => {
        return React.Children.map(children, child => {
            if (typeof child === "string") {
                return child.split(citationRegex).map((part, index) => {
                    if (index % 2 === 0) {
                        return part;
                    }

                    const citationId = part;
                    if (imageCitationMap.has(citationId)) {
                        const citation = imageCitationMap.get(citationId);
                        return citation ? inlineImage(`${citationId}-${index}`, citation) : citationHit(`${citationId}-${index}`, citationId);
                    }
                    if (textCitationMap.has(citationId)) {
                        return citationHit(`${citationId}-${index}`, citationId);
                    }
                    return citationHit(`${citationId}-${index}`, citationId);
                });
            }
            return child;
        });
    };

    const formatTimestamp = (timestamp?: number) => {
        if (!timestamp) {
            return "";
        }
        return new Date(timestamp).toLocaleString();
    };

    const getCurProcessingStep = (requestId: string): Record<string, ProcessingStepsMessage[]> => {
        const processingSteps = processingStepMsg[requestId];

        return { [requestId]: processingSteps };
    };

    return (
        <>
            <div className="chat-container" ref={chatContainerRef}>
                {messagesGroupedByRequestId.map((group, index) => (
                    <>
                        <div key={index} className="chat-message-group">
                            {group.map((message, msgIndex) => {
                                const hasCitations = messageHasCitations(message);
                                const textCitationMap = buildCitationMap(message.textCitations || []);
                                const imageCitationMap = buildCitationMap(message.imageCitations || []);

                                if (message.type === ThreadType.Answer) {
                                    messageToBeCopied[message.request_id] = message.answerPartial?.answer || "";
                                }

                                return (
                                    <React.Fragment key={msgIndex}>
                                        {message.timestamp &&
                                            (message.role === RoleType.User ||
                                                (message.role === RoleType.Assistant && message.type === ThreadType.Answer)) && (
                                                <div
                                                    className={`chat-timestamp ${
                                                        message.role === RoleType.User
                                                            ? "chat-timestamp-user"
                                                            : "chat-timestamp-assistant"
                                                    }`}
                                                >
                                                    {message.role === RoleType.User ? "Submitted" : "Responded"} at{" "}
                                                    {formatTimestamp(message.timestamp)}
                                                </div>
                                            )}
                                        <div
                                            className={`chat-message ${message.role === RoleType.User ? "user-chat" : message.type === ThreadType.Info ? "info" : ""}`}
                                        >
                                            {message.type === ThreadType.Message && <a>{message.message}</a>}
                                            {
                                                //message.type === ThreadType.Info && <Caption1 italic>{message.message}</Caption1>
                                            }
                                            {message.type === ThreadType.Answer && (
                                                <ReactMarkdown
                                                    components={{
                                                        p: ({ children }) => (
                                                            <p>{renderWithCitations(children, textCitationMap, imageCitationMap)}</p>
                                                        ),
                                                        li: ({ children }) => (
                                                            <li>{renderWithCitations(children, textCitationMap, imageCitationMap)}</li>
                                                        )
                                                    }}
                                                    remarkPlugins={[remarkGfm]}
                                                >
                                                    {message.answerPartial?.answer}
                                                </ReactMarkdown>
                                            )}
                                            {message.type === ThreadType.Error && (
                                                <div className="error-message">
                                                    <p style={{ color: "red", fontWeight: "normal" }}>{message.message || "An error occurred."}</p>
                                                </div>
                                            )}

                                            {(message.type === ThreadType.Error ||
                                                message.type === ThreadType.Citation ||
                                                message.type === ThreadType.Answer) && (
                                                <>
                                                    <div className="chat-footer">
                                                        <Tooltip
                                                            onVisibleChange={() => {
                                                                setShowCopied(false);
                                                            }}
                                                            content={showCopied ? "Copied" : "Copy response"}
                                                            relationship="label"
                                                        >
                                                            <Button
                                                                size={"small"}
                                                                icon={<Copy20Regular />}
                                                                iconPosition="after"
                                                                style={{ backgroundColor: "transparent", border: "none" }}
                                                                onClick={() => {
                                                                    const textToCopy = messageToBeCopied[message.request_id] || message.message || "";
                                                                    navigator.clipboard.writeText(textToCopy).catch(err => {
                                                                        console.error("Failed to copy text: ", err);
                                                                    });
                                                                    setShowCopied(true);
                                                                }}
                                                            ></Button>
                                                        </Tooltip>
                                                        <Tooltip content="Process steps" relationship="label">
                                                            <Button
                                                                disabled={Object.keys(processingStepMsg || {}).length === 0}
                                                                size={"small"}
                                                                icon={<BrainCircuit20Regular />}
                                                                iconPosition="after"
                                                                style={{ backgroundColor: "transparent", border: "none" }}
                                                                onClick={() => {
                                                                    setShowProcessingSteps(true);
                                                                    setProcessRequestId(message.request_id);
                                                                }}
                                                            ></Button>
                                                        </Tooltip>
                                                    </div>
                                                    {hasCitations && (
                                                        <Citations
                                                            imageCitations={message.imageCitations || []}
                                                            textCitations={message.textCitations || []}
                                                            highlightedCitation={highlightedCitation}
                                                        />
                                                    )}
                                                </>
                                            )}
                                        </div>
                                    </React.Fragment>
                                );
                            })}
                        </div>
                    </>
                ))}
                <ProcessingSteps
                    showProcessingSteps={showProcessingSteps}
                    processingStepMsg={getCurProcessingStep(processRequestId)}
                    toggleEditor={() => {
                        setShowProcessingSteps(!showProcessingSteps);
                    }}
                />
            </div>
        </>
    );
};

export default ChatContent;
