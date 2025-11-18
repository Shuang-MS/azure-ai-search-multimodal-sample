import { MutableRefObject, useCallback, useEffect, useRef, useState } from "react";

interface CompletedRequest {
    requestId: string;
    answer: string;
}

interface StreamingChunk {
    requestId: string;
    chunk: string;
    chunkId: string;
}

interface UseMuteSpeechOptions {
    supportsSpeech: boolean;
    speak: (text: string) => Promise<void>;
    enqueueSpeechChunk: (text: string) => Promise<void>;
    stopSpeaking: () => Promise<void>;
    completedRequest?: CompletedRequest;
    streamingChunk?: StreamingChunk;
}

const CITATION_PATTERN_SOURCE =
    "(?:[^\\]]+_(?:text_sections|normalized_images)_\\d+|[A-Za-z0-9]{12}(?:_[^\\]]+)?)";
const BRACKETED_CITATION_REGEX = new RegExp(`\\[${CITATION_PATTERN_SOURCE}\\]`, "g");
const CITATION_CONTENT_REGEX = new RegExp(`^${CITATION_PATTERN_SOURCE}$`);

const normalizeWhitespace = (text: string): string =>
    text
        .replace(/\s+([.,!?;:])/g, "$1")
        .replace(/\s{2,}/g, " ")
        .trim();

const sanitizeCompleteAnswer = (text: string): string => {
    if (!text) {
        return "";
    }
    const noCitations = text.replace(BRACKETED_CITATION_REGEX, "");
    return normalizeWhitespace(noCitations);
};

export default function useMuteSpeech({
    supportsSpeech,
    speak,
    enqueueSpeechChunk,
    stopSpeaking,
    completedRequest,
    streamingChunk
}: UseMuteSpeechOptions) {
    const [muteMode, setMuteMode] = useState(false);
    const [lastNarratedRequestId, setLastNarratedRequestId] = useState<string>();
    const lastChunkIdRef = useRef<string>();
    const lastStreamingRequestIdRef = useRef<string>();
    const streamingNarratedRequestsRef = useRef<Set<string>>(new Set());
    const streamingCitationBufferRef = useRef<string>("");

    const toggleMute = useCallback(() => {
        setMuteMode(prev => !prev);
    }, []);

    useEffect(() => {
        if (!muteMode) {
            return;
        }
        stopSpeaking();
    }, [muteMode, stopSpeaking]);

    useEffect(() => {
        if (!streamingChunk?.chunkId) {
            return;
        }
        if (lastChunkIdRef.current === streamingChunk.chunkId) {
            return;
        }
        lastChunkIdRef.current = streamingChunk.chunkId;

        if (streamingChunk.requestId !== lastStreamingRequestIdRef.current) {
            streamingCitationBufferRef.current = "";
            lastStreamingRequestIdRef.current = streamingChunk.requestId;
        }

        streamingNarratedRequestsRef.current.add(streamingChunk.requestId);

        if (!supportsSpeech || muteMode) {
            return;
        }

        const cleanChunk = sanitizeStreamingChunk(streamingChunk.chunk, streamingCitationBufferRef);
        if (!cleanChunk) {
            return;
        }

        console.info("Speech chunk queued for TTS", {
            queuedAt: new Date().toISOString(),
            text: cleanChunk
        });

        void enqueueSpeechChunk(cleanChunk);
    }, [enqueueSpeechChunk, muteMode, streamingChunk, supportsSpeech]);

    useEffect(() => {
        if (!completedRequest?.answer?.trim() || !completedRequest.requestId || !supportsSpeech) {
            return;
        }

        if (
            completedRequest.requestId === lastNarratedRequestId ||
            streamingNarratedRequestsRef.current.has(completedRequest.requestId)
        ) {
            return;
        }

        if (muteMode) {
            setLastNarratedRequestId(completedRequest.requestId);
            return;
        }

        const sanitizedAnswer = sanitizeCompleteAnswer(completedRequest.answer);
        if (!sanitizedAnswer) {
            setLastNarratedRequestId(completedRequest.requestId);
            return;
        }

        speak(sanitizedAnswer);
        setLastNarratedRequestId(completedRequest.requestId);
    }, [completedRequest, lastNarratedRequestId, muteMode, speak, supportsSpeech]);

    return {
        muteMode,
        toggleMute
    };
}

function sanitizeStreamingChunk(chunk: string, bufferRef: MutableRefObject<string>): string {
    if (!chunk) {
        return "";
    }

    let sanitized = "";
    let buffer = bufferRef.current;
    let readingCitation = buffer.length > 0;

    for (const char of chunk) {
        if (readingCitation) {
            buffer += char;
            if (char === "]") {
                const innerContent = buffer.slice(1, -1).trim();
                if (!CITATION_CONTENT_REGEX.test(innerContent)) {
                    sanitized += buffer;
                }
                buffer = "";
                readingCitation = false;
            }
            continue;
        }

        if (char === "[") {
            buffer = "[";
            readingCitation = true;
            continue;
        }

        sanitized += char;
    }

    bufferRef.current = buffer;
    return normalizeWhitespace(sanitized);
}
