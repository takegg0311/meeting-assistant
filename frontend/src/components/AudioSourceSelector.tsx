import type { AudioSource } from "../types/messages";
import { isDisplayAudioSupported } from "../audio/AudioSourceProvider";

interface Props {
  value: AudioSource;
  disabled: boolean;
  onChange: (source: AudioSource) => void;
}

const displaySupported = isDisplayAudioSupported();

export function AudioSourceSelector({ value, disabled, onChange }: Props) {
  return (
    <fieldset disabled={disabled}>
      <legend>音声ソース</legend>
      <label>
        <input
          type="radio"
          name="audio-source"
          value="microphone"
          checked={value === "microphone"}
          onChange={() => onChange("microphone")}
        />
        マイク
      </label>
      <label title={displaySupported ? "" : "このブラウザではPC音声の取得に対応していません"}>
        <input
          type="radio"
          name="audio-source"
          value="tab_audio"
          checked={value === "tab_audio"}
          disabled={!displaySupported}
          onChange={() => onChange("tab_audio")}
        />
        タブ / システム音声{!displaySupported && "(非対応)"}
      </label>
    </fieldset>
  );
}
