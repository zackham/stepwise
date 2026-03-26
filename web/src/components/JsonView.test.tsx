import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { JsonView } from "./JsonView";

describe("JsonView", () => {
  it("renders null values", () => {
    render(<JsonView data={null} />);
    expect(screen.getByText("null")).toBeInTheDocument();
  });

  it("renders inline strings", () => {
    render(<JsonView data="hello world" />);
    expect(screen.getByText("hello world")).toBeInTheDocument();
  });

  it("renders numbers", () => {
    render(<JsonView data={42} />);
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("renders booleans", () => {
    render(<JsonView data={true} />);
    expect(screen.getByText("true")).toBeInTheDocument();
  });

  describe("JSON string indicator", () => {
    it("shows 'JSON string' badge when value is a parseable JSON object string", () => {
      render(<JsonView data='{"key": "value"}' />);
      expect(screen.getByText("JSON string")).toBeInTheDocument();
    });

    it("shows 'JSON string' badge for JSON array strings", () => {
      render(<JsonView data='[1, 2, 3]' />);
      expect(screen.getByText("JSON string")).toBeInTheDocument();
    });

    it("does not show badge for plain strings", () => {
      render(<JsonView data="hello world" />);
      expect(screen.queryByText("JSON string")).not.toBeInTheDocument();
    });

    it("does not show badge for native objects", () => {
      render(<JsonView data={{ key: "value" }} />);
      expect(screen.queryByText("JSON string")).not.toBeInTheDocument();
    });

    it("does not show badge for native arrays", () => {
      render(<JsonView data={[1, 2, 3]} />);
      expect(screen.queryByText("JSON string")).not.toBeInTheDocument();
    });

    it("does not show badge for strings starting with { that aren't valid JSON", () => {
      render(<JsonView data="{not valid json}" />);
      expect(screen.queryByText("JSON string")).not.toBeInTheDocument();
    });

    it("toggles to raw string view when badge is clicked", () => {
      render(<JsonView data='{"key": "value"}' />);
      fireEvent.click(screen.getByText("JSON string"));
      expect(screen.getByText("raw")).toBeInTheDocument();
      expect(screen.getByText('{"key": "value"}')).toBeInTheDocument();
    });

    it("toggles back to parsed view from raw view", () => {
      render(<JsonView data='{"key": "value"}' />);
      fireEvent.click(screen.getByText("JSON string"));
      expect(screen.getByText("raw")).toBeInTheDocument();
      fireEvent.click(screen.getByText("raw"));
      expect(screen.getByText("JSON string")).toBeInTheDocument();
    });

    it("shows badge for empty JSON object string", () => {
      render(<JsonView data="{}" />);
      expect(screen.getByText("JSON string")).toBeInTheDocument();
    });

    it("shows badge for empty JSON array string", () => {
      render(<JsonView data="[]" />);
      expect(screen.getByText("JSON string")).toBeInTheDocument();
    });

    it("renders with name prop when in parsed mode", () => {
      render(<JsonView data='{"a":1}' name="config" />);
      expect(screen.getByText("JSON string")).toBeInTheDocument();
      expect(screen.getByText(/config/)).toBeInTheDocument();
    });

    it("renders with name prop when in raw mode", () => {
      render(<JsonView data='{"a":1}' name="config" />);
      fireEvent.click(screen.getByText("JSON string"));
      expect(screen.getByText(/config/)).toBeInTheDocument();
      expect(screen.getByText("raw")).toBeInTheDocument();
    });
  });
});
