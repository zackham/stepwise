import { useRef, useEffect } from "react";
import { EditorView } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { basicSetup } from "codemirror";
import { yaml } from "@codemirror/lang-yaml";
import { oneDark } from "@codemirror/theme-one-dark";
import { useTheme } from "@/hooks/useTheme";

interface YamlEditorProps {
  value: string;
  onChange: (value: string) => void;
}

export function YamlEditor({ value, onChange }: YamlEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const theme = useTheme();
  const isDark = theme === "dark";

  useEffect(() => {
    if (!containerRef.current) return;

    const extensions = [
      basicSetup,
      yaml(),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          onChangeRef.current(update.state.doc.toString());
        }
      }),
      EditorView.theme({
        "&": { height: "100%", fontSize: "13px" },
        ".cm-scroller": { overflow: "auto" },
        ".cm-content": {
          fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        },
      }),
    ];
    if (isDark) {
      extensions.push(oneDark);
    }

    const view = new EditorView({
      state: EditorState.create({
        doc: value,
        extensions,
      }),
      parent: containerRef.current,
    });

    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, [isDark]); // eslint-disable-line react-hooks/exhaustive-deps

  // Handle external value changes (load new flow)
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const currentDoc = view.state.doc.toString();
    if (value !== currentDoc) {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: value },
      });
    }
  }, [value]);

  return <div ref={containerRef} className="h-full w-full overflow-hidden bg-white dark:bg-transparent" />;
}
