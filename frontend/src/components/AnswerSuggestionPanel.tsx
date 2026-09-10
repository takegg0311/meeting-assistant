import { useState } from "react";
import type { AnswerSuggestionEvent } from "../types/messages";

interface Props {
  suggestions: AnswerSuggestionEvent[];
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // クリップボードAPIが使えない環境(非HTTPS等)では選択してコピーしてもらう。
      setCopied(false);
    }
  };

  return (
    <button type="button" className="answer-copy" onClick={handleCopy}>
      {copied ? "コピーしました" : "コピー"}
    </button>
  );
}

function AnswerCard({ suggestion }: { suggestion: AnswerSuggestionEvent }) {
  if (suggestion.status === "generating") {
    return (
      <li className="answer-card answer-card-generating">
        <p className="answer-body">回答案を生成しています…</p>
      </li>
    );
  }

  if (suggestion.status === "error") {
    return (
      <li className="answer-card answer-card-error">
        <p className="answer-body">{suggestion.message || "回答提案の生成に失敗しました。"}</p>
      </li>
    );
  }

  return (
    <li className="answer-card">
      <p className="answer-body">{suggestion.answer}</p>
      <div className="answer-card-footer">
        <CopyButton text={suggestion.answer} />
      </div>
    </li>
  );
}

export function AnswerSuggestionPanel({ suggestions }: Props) {
  if (suggestions.length === 0) {
    return null;
  }

  return (
    <section className="answer-panel">
      <h2>回答提案</h2>
      {/* 新しいものを上に積む(連打時に最新が埋もれないようにする) */}
      <ul className="answer-list">
        {[...suggestions].reverse().map((suggestion) => (
          <AnswerCard key={suggestion.request_id} suggestion={suggestion} />
        ))}
      </ul>
    </section>
  );
}
