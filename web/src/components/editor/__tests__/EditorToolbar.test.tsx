import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EditorToolbar } from "../EditorToolbar";

describe("EditorToolbar", () => {
  it("displays flow name", () => {
    render(
      <EditorToolbar
        flowName="research"
        isDirty={false}
        isSaving={false}
        onSave={() => {}}
        onDiscard={() => {}}
        parseErrors={[]}
      />
    );
    expect(screen.getByText("research")).toBeDefined();
  });

  it("shows dirty indicator when dirty", () => {
    const { container } = render(
      <EditorToolbar
        flowName="research"
        isDirty={true}
        isSaving={false}
        onSave={() => {}}
        onDiscard={() => {}}
        parseErrors={[]}
      />
    );
    const dot = container.querySelector(".bg-amber-400");
    expect(dot).toBeDefined();
  });

  it("save button disabled when not dirty", () => {
    render(
      <EditorToolbar
        flowName="research"
        isDirty={false}
        isSaving={false}
        onSave={() => {}}
        onDiscard={() => {}}
        parseErrors={[]}
      />
    );
    const saveBtn = screen.getByText("Save").closest("button")!;
    expect(saveBtn.disabled).toBe(true);
  });

  it("save button enabled when dirty", () => {
    render(
      <EditorToolbar
        flowName="research"
        isDirty={true}
        isSaving={false}
        onSave={() => {}}
        onDiscard={() => {}}
        parseErrors={[]}
      />
    );
    const saveBtn = screen.getByText("Save").closest("button")!;
    expect(saveBtn.disabled).toBe(false);
  });

  it("calls onSave when save clicked", () => {
    const onSave = vi.fn();
    render(
      <EditorToolbar
        flowName="research"
        isDirty={true}
        isSaving={false}
        onSave={onSave}
        onDiscard={() => {}}
        parseErrors={[]}
      />
    );
    fireEvent.click(screen.getByText("Save"));
    expect(onSave).toHaveBeenCalled();
  });

  it("calls onDiscard when discard clicked", () => {
    const onDiscard = vi.fn();
    render(
      <EditorToolbar
        flowName="research"
        isDirty={true}
        isSaving={false}
        onSave={() => {}}
        onDiscard={onDiscard}
        parseErrors={[]}
      />
    );
    fireEvent.click(screen.getByText("Discard"));
    expect(onDiscard).toHaveBeenCalled();
  });

  it("shows saving state", () => {
    render(
      <EditorToolbar
        flowName="research"
        isDirty={true}
        isSaving={true}
        onSave={() => {}}
        onDiscard={() => {}}
        parseErrors={[]}
      />
    );
    expect(screen.getByText("Saving...")).toBeDefined();
  });

  it("displays parse errors", () => {
    render(
      <EditorToolbar
        flowName="research"
        isDirty={false}
        isSaving={false}
        onSave={() => {}}
        onDiscard={() => {}}
        parseErrors={["Invalid YAML: unexpected indent"]}
      />
    );
    expect(
      screen.getByText("Invalid YAML: unexpected indent")
    ).toBeDefined();
  });
});
