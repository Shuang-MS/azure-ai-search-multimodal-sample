import { Caption1, FluentProvider, webDarkTheme, webLightTheme } from "@fluentui/react-components";
import { Title1 } from "@fluentui/react-components";

import "./App.css";
import ChatContent from "../components/ChatContent";
import { Header } from "../components/Header";
import { NavBar } from "../components/NavBar";
import Samples from "../components/Samples";
import SearchInput from "../components/SearchInput";
import useChat from "../hooks/useChat";
import useConfig from "../hooks/useConfig";
import useTheme from "../hooks/useTheme";
import { IntroTitle } from "../api/defaults";
import { useState } from "react";
import useSpeech from "../hooks/useSpeech";
import useMuteSpeech from "../hooks/useMuteSpeech";

function App() {
    const { config, setConfig, indexes } = useConfig();
    const { thread, processingStepsMessage, chats, isLoading, handleQuery, onNewChat, completedRequest, streamingChunk } = useChat(config);
    const { darkMode, setDarkMode } = useTheme();
    const [newQ, setnewQ] = useState(false);
    const speech = useSpeech();
    const { supportsSpeech, speak, enqueueSpeechChunk, stopSpeaking } = speech;
    const { muteMode, toggleMute } = useMuteSpeech({
        supportsSpeech,
        speak,
        enqueueSpeechChunk,
        stopSpeaking,
        completedRequest,
        streamingChunk
    });

    const runQuery = (query: string) => {
        stopSpeaking();
        handleQuery(query);
    };

    return (
        <FluentProvider theme={darkMode ? webDarkTheme : webLightTheme}>
            <div className="container">
                <Header
                    darkMode={darkMode}
                    toggleMode={setDarkMode}
                    muteMode={muteMode}
                    onToggleMute={toggleMute}
                    supportsSpeech={supportsSpeech}
                />

                <div className="content-wrapper">
                    {thread.length || newQ ? (
                        <>
                            <NavBar config={config} indexes={indexes} setConfig={setConfig} onNewChat={onNewChat} chats={Object.values(chats || {})} />

                            <div className="content">
                                {thread.length ? <ChatContent thread={thread} processingStepMsg={processingStepsMessage} /> : <></>}
                                <div className="search-footer">
                                    <SearchInput onSearch={runQuery} isLoading={isLoading} speech={speech} />
                                </div>
                            </div>
                        </>
                    ) : (
                        <>
                            <div className="intro-card">
                                <Title1 block align="center">
                                    {IntroTitle}
                                </Title1>
                                <br />
                                <Caption1 style={{ fontWeight: "bold" }} block align="center">
                                    Choose an example to start with...
                                </Caption1>
                                <Samples
                                    handleQuery={(q, isNew) => {
                                        if (isNew) {
                                            setnewQ(true);
                                        } else {
                                            runQuery(q);
                                        }
                                    }}
                                />
                                <div className="search-footer">
                                    <div className="intro">
                                        <SearchInput onSearch={runQuery} isLoading={isLoading} speech={speech} />
                                    </div>
                                </div>
                            </div>
                        </>
                    )}
                </div>
            </div>
        </FluentProvider>
    );
}

export default App;
