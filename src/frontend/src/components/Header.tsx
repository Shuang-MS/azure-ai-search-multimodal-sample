import { Button, Divider, Switch, Title2 } from "@fluentui/react-components";

import "./Header.css";

interface Props {
    toggleMode: (mode: boolean) => void;
    darkMode: boolean;
    muteMode: boolean;
    onToggleMute: () => void;
    supportsSpeech: boolean;
}

export const Header = ({ toggleMode, darkMode, muteMode, onToggleMute, supportsSpeech }: Props) => {
    return (
        <>
            <div className="header">
                <Title2> Multimodal RAG + Azure AI Search</Title2>
                <div className="header-right">
                    <Button
                        aria-pressed={muteMode}
                        appearance={muteMode ? "primary" : "secondary"}
                        disabled={!supportsSpeech}
                        onClick={onToggleMute}
                        title={supportsSpeech ? undefined : "Speech is unavailable in this browser."}
                    >
                        {muteMode ? "Unmute" : "Mute"}
                    </Button>
                    <Switch
                        checked={darkMode}
                        label={`Dark Mode`}
                        onChange={() => {
                            toggleMode(!darkMode);
                        }}
                    />
                </div>
            </div>
            <Divider />
        </>
    );
};
