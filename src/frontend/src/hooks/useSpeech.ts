import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    AudioConfig,
    PushAudioOutputStreamCallback,
    ResultReason,
    SpeechConfig,
    SpeechRecognizer,
    SpeechSynthesizer
} from "microsoft-cognitiveservices-speech-sdk";

type FinalResultHandler = (text: string) => void;

interface TokenResponse {
    token: string;
    region: string;
    voice?: string;
}

export interface SpeechControls {
    supportsSpeech: boolean;
    listening: boolean;
    transcript: string;
    speaking: boolean;
    error?: string;
    startListening: (onFinalResult?: FinalResultHandler) => Promise<void>;
    stopListening: () => Promise<void>;
    speak: (text: string) => Promise<void>;
    enqueueSpeechChunk: (text: string) => Promise<void>;
    stopSpeaking: () => Promise<void>;
}

const TOKEN_EXPIRATION_MS = 9 * 60 * 1000;

interface AudioContextWindow extends Window {
    AudioContext?: typeof AudioContext;
    webkitAudioContext?: typeof AudioContext;
}

const concatAudioChunks = (chunks: Uint8Array[]) => {
    if (!chunks.length) {
        return new Uint8Array();
    }
    const totalLength = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
    const merged = new Uint8Array(totalLength);
    let offset = 0;
    chunks.forEach(chunk => {
        merged.set(chunk, offset);
        offset += chunk.byteLength;
    });
    return merged;
};

export default function useSpeech(): SpeechControls {
    const supportsSpeech =
        typeof window !== "undefined" &&
        typeof navigator !== "undefined" &&
        Boolean(navigator.mediaDevices);

    const [listening, setListening] = useState(false);
    const [speaking, setSpeaking] = useState(false);
    const [transcript, setTranscript] = useState("");
    const [error, setError] = useState<string>();

    const speechConfigRef = useRef<SpeechConfig>();
    const recognizerRef = useRef<SpeechRecognizer>();
    const synthesizerRef = useRef<SpeechSynthesizer>();
    const synthesizerConfigRef = useRef<SpeechConfig>();
    const tokenInfoRef = useRef<{ expiresAt: number; response: TokenResponse }>();
    const finalResultHandlerRef = useRef<FinalResultHandler>();
    const speechQueueRef = useRef<string[]>([]);
    const queueProcessingRef = useRef(false);
    const pendingSpeechResolveRef = useRef<(() => void) | undefined>();
    const audioContextRef = useRef<AudioContext>();
    const audioSourceRef = useRef<AudioBufferSourceNode>();
    const chunkCollectorRef = useRef<Uint8Array[] | undefined>();

    const fetchToken = useCallback(async (): Promise<TokenResponse> => {
        const response = await fetch("/speech/token");
        if (!response.ok) {
            throw new Error("Unable to reach Azure Speech token endpoint.");
        }
        return response.json();
    }, []);

    const ensureSpeechConfig = useCallback(async () => {
        if (!supportsSpeech) {
            throw new Error("Speech is not supported in this browser.");
        }

        const now = Date.now();
        if (tokenInfoRef.current && speechConfigRef.current && now < tokenInfoRef.current.expiresAt) {
            return speechConfigRef.current;
        }

        const tokenResponse = await fetchToken();
        tokenInfoRef.current = {
            response: tokenResponse,
            expiresAt: Date.now() + TOKEN_EXPIRATION_MS
        };

        const config = SpeechConfig.fromAuthorizationToken(tokenResponse.token, tokenResponse.region);
        config.speechRecognitionLanguage = "en-US";
        if (tokenResponse.voice) {
            config.speechSynthesisVoiceName = tokenResponse.voice;
        }
        speechConfigRef.current = config;
        return config;
    }, [fetchToken, supportsSpeech]);

    const cleanupRecognizer = useCallback(
        () =>
            new Promise<void>(resolve => {
                const recognizer = recognizerRef.current;
                if (!recognizer) {
                    setListening(false);
                    setTranscript("");
                    resolve();
                    return;
                }

                recognizer.stopContinuousRecognitionAsync(
                    () => {
                        recognizer.close();
                        recognizerRef.current = undefined;
                        setListening(false);
                        setTranscript("");
                        resolve();
                    },
                    err => {
                        console.error("Failed to stop speech recognizer", err);
                        recognizer.close();
                        recognizerRef.current = undefined;
                        setListening(false);
                        setTranscript("");
                        resolve();
                    }
                );
            }),
        []
    );

    const stopListening = useCallback(async () => {
        await cleanupRecognizer();
    }, [cleanupRecognizer]);

    const startListening = useCallback(
        async (onFinalResult?: FinalResultHandler) => {
            if (!supportsSpeech) {
                setError("Speech recognition is not supported in this browser.");
                return;
            }

            if (listening) {
                return;
            }

            try {
                setError(undefined);
                finalResultHandlerRef.current = onFinalResult;
                const speechConfig = await ensureSpeechConfig();
                const audioConfig = AudioConfig.fromDefaultMicrophoneInput();
                const recognizer = new SpeechRecognizer(speechConfig, audioConfig);

                recognizer.recognizing = (_, event) => {
                    if (event.result?.reason === ResultReason.RecognizingSpeech) {
                        setTranscript(event.result.text || "");
                    }
                };

                recognizer.recognized = (_, event) => {
                    if (event.result?.reason === ResultReason.RecognizedSpeech) {
                        const text = (event.result.text || "").trim();
                        setTranscript(text);
                        if (text && finalResultHandlerRef.current) {
                            finalResultHandlerRef.current(text);
                        }
                        finalResultHandlerRef.current = undefined;
                        cleanupRecognizer();
                    } else if (event.result?.reason === ResultReason.NoMatch) {
                        setError("I couldn't quite hear that. Please try again.");
                    }
                };

                recognizer.canceled = (_, event) => {
                    setError(event.errorDetails || "Speech recognition was canceled.");
                    finalResultHandlerRef.current = undefined;
                    cleanupRecognizer();
                };

                recognizer.sessionStopped = () => {
                    finalResultHandlerRef.current = undefined;
                    cleanupRecognizer();
                };

                recognizer.startContinuousRecognitionAsync(
                    () => {
                        recognizerRef.current = recognizer;
                        setTranscript("");
                        setListening(true);
                    },
                    err => {
                        const errorMessage =
                            typeof err === "string"
                                ? err
                                : err && typeof err === "object" && "message" in err
                                  ? String((err as { message?: string }).message)
                                  : "";
                        recognizer.close();
                        setError(errorMessage || "Unable to access the microphone.");
                    }
                );
            } catch (err) {
                setError(err instanceof Error ? err.message : "Unable to start speech recognition.");
            }
        },
        [cleanupRecognizer, ensureSpeechConfig, listening, supportsSpeech]
    );

    const stopAudioPlayback = useCallback(() => {
        if (audioSourceRef.current) {
            try {
                audioSourceRef.current.stop();
            } catch (err) {
                console.error("Failed to stop audio playback", err);
            }
            audioSourceRef.current.disconnect();
            audioSourceRef.current = undefined;
        }
    }, []);

    const resetSynthesizer = useCallback(() => {
        chunkCollectorRef.current = undefined;
        const synthesizer = synthesizerRef.current;
        if (synthesizer) {
            synthesizer.close();
            synthesizerRef.current = undefined;
        }
        synthesizerConfigRef.current = undefined;
    }, []);

    const stopSpeaking = useCallback(async () => {
        speechQueueRef.current = [];
        resetSynthesizer();
        stopAudioPlayback();
        setSpeaking(false);
        pendingSpeechResolveRef.current?.();
        pendingSpeechResolveRef.current = undefined;
    }, [resetSynthesizer, stopAudioPlayback]);

    const ensureAudioContext = useCallback((): AudioContext => {
        const existingContext = audioContextRef.current;
        if (existingContext) {
            return existingContext;
        }

        if (typeof window === "undefined") {
            throw new Error("AudioContext is not available in this environment.");
        }

        const audioWindow = window as AudioContextWindow;
        const AudioContextCtor = audioWindow.AudioContext ?? audioWindow.webkitAudioContext;
        if (!AudioContextCtor) {
            throw new Error("AudioContext constructor is unavailable.");
        }

        const context = new AudioContextCtor();
        audioContextRef.current = context;
        return context;
    }, []);

    const playAudioBytes = useCallback(
        async (audioBytes: Uint8Array) => {
            if (!audioBytes?.length) {
                return;
            }

            const audioCtx = ensureAudioContext();
            if (audioCtx.state === "suspended") {
                try {
                    await audioCtx.resume();
                } catch (err) {
                    console.warn("Unable to resume AudioContext", err);
                }
            }

            const sourceBuffer = audioBytes.buffer;
            if (!(sourceBuffer instanceof ArrayBuffer)) {
                throw new Error("SharedArrayBuffer is not supported for audio playback.");
            }
            const arrayBuffer = sourceBuffer.slice(
                audioBytes.byteOffset,
                audioBytes.byteOffset + audioBytes.byteLength
            );

            const decodedBuffer = await new Promise<AudioBuffer>((resolve, reject) => {
                audioCtx.decodeAudioData(arrayBuffer, resolve, reject);
            });

            await new Promise<void>((resolve, reject) => {
                const source = audioCtx.createBufferSource();
                audioSourceRef.current = source;
                source.buffer = decodedBuffer;
                source.connect(audioCtx.destination);
                source.onended = () => {
                    if (audioSourceRef.current === source) {
                        audioSourceRef.current.disconnect();
                        audioSourceRef.current = undefined;
                    }
                    resolve();
                };
                try {
                    source.start();
                } catch (err) {
                    reject(err instanceof Error ? err : new Error("Unable to start audio playback."));
                }
            });
        },
        [ensureAudioContext]
    );

    const ensureSynthesizer = useCallback(async () => {
        const speechConfig = await ensureSpeechConfig();
        const existingSynth = synthesizerRef.current;
        const existingConfig = synthesizerConfigRef.current;

        if (existingSynth && existingConfig === speechConfig) {
            return existingSynth;
        }

        if (existingSynth) {
            existingSynth.close();
        }

        const pushStream = new (class extends PushAudioOutputStreamCallback {
            write(dataBuffer: ArrayBuffer) {
                const collector = chunkCollectorRef.current;
                if (collector) {
                    collector.push(new Uint8Array(dataBuffer));
                }
            }
            close() {
                return;
            }
        })();

        console.log("Created new SpeechSynthesizer");
        const audioConfig = AudioConfig.fromStreamOutput(pushStream);
        const synthesizer = new SpeechSynthesizer(speechConfig, audioConfig);
        synthesizerRef.current = synthesizer;
        synthesizerConfigRef.current = speechConfig;
        return synthesizer;
    }, [ensureSpeechConfig]);

    const synthesizeText = useCallback(
        async (text: string, interrupt: boolean) => {
            if (!supportsSpeech || !text?.trim()) {
                return;
            }

            try {
                setError(undefined);
                if (interrupt) {
                    await stopSpeaking();
                }

                const synthesizer = await ensureSynthesizer();
                const audioChunks: Uint8Array[] = [];
                chunkCollectorRef.current = audioChunks;
                setSpeaking(true);

                await new Promise<void>((resolve, reject) => {
                    const cleanup = () => {
                        if (chunkCollectorRef.current === audioChunks) {
                            chunkCollectorRef.current = undefined;
                        }
                        pendingSpeechResolveRef.current = undefined;
                    };

                    const reportError = (errorMessage?: string) => {
                        setError(errorMessage || "Speech synthesis failed.");
                        stopAudioPlayback();
                        setSpeaking(false);
                    };

                    pendingSpeechResolveRef.current = resolve;
                    synthesizer.speakTextAsync(
                        text,
                        result => {
                            void (async () => {
                                try {
                                    if (result.reason !== ResultReason.SynthesizingAudioCompleted) {
                                        throw new Error(result.errorDetails || "Failed to synthesize speech.");
                                    }

                                    const bufferedAudio = audioChunks.length
                                        ? concatAudioChunks(audioChunks)
                                        : new Uint8Array(result.audioData);

                                    if (bufferedAudio.length) {
                                        await playAudioBytes(bufferedAudio);
                                    }

                                    setSpeaking(false);
                                    resolve();
                                } catch (error) {
                                    resetSynthesizer();
                                    reject(error instanceof Error ? error : new Error("Speech synthesis failed."));
                                } finally {
                                    cleanup();
                                }
                            })();
                        },
                        err => {
                            const errorMessage =
                                typeof err === "string"
                                    ? err
                                    : err && typeof err === "object" && "message" in err
                                      ? String((err as { message?: string }).message)
                                      : "";
                            reportError(errorMessage);
                            resetSynthesizer();
                            cleanup();
                            resolve();
                        }
                    );
                });
            } catch (err) {
                setSpeaking(false);
                setError(err instanceof Error ? err.message : "Unable to synthesize speech.");
            }
        },
        [ensureSynthesizer, playAudioBytes, resetSynthesizer, stopAudioPlayback, stopSpeaking, supportsSpeech]
    );

    const processSpeechQueue = useCallback(async () => {
        if (queueProcessingRef.current) {
            return;
        }
        queueProcessingRef.current = true;
        try {
            while (speechQueueRef.current.length > 0) {
                const nextChunk = speechQueueRef.current.shift();
                if (!nextChunk) {
                    continue;
                }
                await synthesizeText(nextChunk, false);
            }
        } finally {
            queueProcessingRef.current = false;
        }
    }, [synthesizeText]);

    const speak = useCallback(
        async (text: string) => {
            await synthesizeText(text, true);
        },
        [synthesizeText]
    );

    const enqueueSpeechChunk = useCallback(
        async (text: string) => {
            if (!supportsSpeech || !text?.trim()) {
                return;
            }
            speechQueueRef.current.push(text);
            await processSpeechQueue();
        },
        [processSpeechQueue, supportsSpeech]
    );

    useEffect(() => {
        return () => {
            stopListening();
            stopSpeaking();
        };
    }, [stopListening, stopSpeaking]);

    return useMemo(
        () => ({
            supportsSpeech,
            listening,
            transcript,
            speaking,
            error,
            startListening,
            stopListening,
            speak,
            enqueueSpeechChunk,
            stopSpeaking
        }),
        [
            enqueueSpeechChunk,
            error,
            listening,
            speak,
            speaking,
            startListening,
            stopListening,
            stopSpeaking,
            supportsSpeech,
            transcript
        ]
    );
}
