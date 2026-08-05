import type { SttProviderName } from "../types/messages";

interface Props {
  value: SttProviderName;
  disabled: boolean;
  onChange: (provider: SttProviderName) => void;
}

const OPTIONS: { value: SttProviderName; label: string }[] = [
  { value: "mock", label: "Mock(開発用)" },
  { value: "cloud_openai", label: "OpenAI Realtime" },
  { value: "cloud_google", label: "Google Cloud STT" },
  { value: "local_whispercpp", label: "whisper.cpp(ローカル)" },
];

export function SttProviderSelector({ value, disabled, onChange }: Props) {
  return (
    <fieldset disabled={disabled}>
      <legend>STTプロバイダ</legend>
      <select value={value} onChange={(e) => onChange(e.target.value as SttProviderName)}>
        {OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </fieldset>
  );
}
