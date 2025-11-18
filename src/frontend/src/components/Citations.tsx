import React from "react";

import { Button, Caption1Strong } from "@fluentui/react-components";

import { Citation } from "../api/models";
import "./Citations.css";

interface CitationsProps {
    highlightedCitation?: string;
    imageCitations: Citation[];
    textCitations: Citation[];
}

const Citations: React.FC<CitationsProps> = ({ imageCitations, textCitations, highlightedCitation }) => {
    const [expanded, setExpanded] = React.useState(false);
    const totalCitations = textCitations.length + imageCitations.length;

    const truncateText = (text?: string, maxLength = 220) => {
        if (!text) return "";
        return text.length > maxLength ? `${text.substring(0, maxLength)}…` : text;
    };

    const renderCitationRow = (citation: Citation, typeLabel: string, index: number) => (
        <div
            key={`${citation.content_id}-${index}`}
            className={`citation-row ${highlightedCitation === `${citation.content_id}` ? "highlighted" : ""}`}
        >
            <div className="citation-row-header">
                <span className="citation-type">{typeLabel}</span>
                <span className="citation-page">Page {citation.locationMetadata.pageNumber}</span>
            </div>
            <div className="citation-document" title={citation.title || citation.docId}>
                {citation.title || citation.docId}
            </div>
            <div className="citation-snippet">{truncateText(citation.text || "Chunk snippet unavailable")}</div>
        </div>
    );

    if (!totalCitations) {
        return null;
    }

    return (
        <div className="citations-panel">
            <div className="citations-header">
                <Caption1Strong>Citations ({totalCitations})</Caption1Strong>
                <Button
                    appearance="subtle"
                    size="small"
                    onClick={() => setExpanded(prev => !prev)}
                    className="citations-toggle"
                >
                    {expanded ? "Hide" : "Show"}
                </Button>
            </div>
            {expanded && (
                <div className="citations-list">
                    {textCitations.map((citation, index) => renderCitationRow(citation, "Text", index))}
                    {imageCitations.map((citation, index) => renderCitationRow(citation, "Image", index))}
                </div>
            )}
        </div>
    );
};

export default Citations;
