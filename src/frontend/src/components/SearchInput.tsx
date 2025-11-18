import React, { useState } from "react";

import { Button, Caption1, Spinner, Tooltip } from "@fluentui/react-components";
import { Mic24Regular, MicOff24Regular, Search20Filled } from "@fluentui/react-icons";

import "./SearchInput.css";
import { SpeechControls } from "../hooks/useSpeech";

interface SearchInputProps {
    isLoading: boolean;
    onSearch: (query: string) => void;
    speech?: SpeechControls;
}

const SearchInput: React.FC<SearchInputProps> = ({ isLoading, onSearch, speech }) => {
    const [query, setQuery] = useState("");

    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setQuery(e.target.value);
    };

    const submitQuery = (value: string) => {
        const trimmed = value.trim();
        if (!trimmed) {
            return;
        }
        onSearch(trimmed);
        setQuery("");
    };

    const handleSearch = () => {
        submitQuery(query);
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") {
            handleSearch();
        }
    };

    const handleMicToggle = async () => {
        if (!speech || isLoading) {
            return;
        }

        if (speech.listening) {
            await speech.stopListening();
            setQuery("");
            return;
        }

        await speech.stopSpeaking();
        speech.startListening(finalText => {
            submitQuery(finalText);
        });
    };

    const inputValue = speech?.listening ? speech.transcript : query;
    const placeholder = speech?.listening ? "Listening..." : "Ask about your data...";

    return (
        <>
            {isLoading && <div className="loading">Generating answer, please wait...</div>}

            <div className="search-container" style={{ boxShadow: "0px 4px 6px rgba(0, 0, 0, 0.1)" }}>
                <input
                    disabled={isLoading || speech?.listening}
                    className="input"
                    type="text"
                    placeholder={placeholder}
                    value={inputValue}
                    onChange={handleInputChange}
                    onKeyDown={handleKeyDown}
                />
                <div className="search-controls">
                    {speech && (
                        <Tooltip
                            relationship="label"
                            content={
                                !speech.supportsSpeech
                                    ? "Microphone unavailable"
                                    : speech.listening
                                      ? "Stop listening"
                                      : "Ask with your voice"
                            }
                        >
                            <Button
                                aria-pressed={speech.listening}
                                className={`mic-button ${speech.listening ? "listening" : ""}`}
                                disabled={!speech.supportsSpeech || isLoading}
                                shape="circular"
                                size="large"
                                appearance={speech.listening ? "primary" : "secondary"}
                                icon={speech.listening ? <MicOff24Regular /> : <Mic24Regular />}
                                onClick={handleMicToggle}
                            />
                        </Tooltip>
                    )}
                    <Button
                        disabled={isLoading}
                        shape="circular"
                        size="large"
                        appearance="primary"
                        icon={isLoading ? <Spinner size="extra-small" /> : <Search20Filled />}
                        onClick={handleSearch}
                    />
                </div>
            </div>
            <Caption1 style={{ marginTop: "5px", color: "lightgray" }} block align="center" italic>
                AI-generated content may be incorrect
            </Caption1>
            {speech?.error && (
                <Caption1 className="speech-status" role="status" align="center">
                    {speech.error}
                </Caption1>
            )}
        </>
    );
};

export default SearchInput;
