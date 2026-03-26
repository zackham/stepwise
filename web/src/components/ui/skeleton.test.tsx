import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Skeleton } from "./skeleton";

describe("Skeleton", () => {
  it("renders with default classes", () => {
    const { container } = render(<Skeleton />);
    const el = container.firstElementChild!;
    expect(el.getAttribute("data-slot")).toBe("skeleton");
    expect(el.className).toContain("animate-pulse");
    expect(el.className).toContain("rounded-md");
  });

  it("merges custom className", () => {
    const { container } = render(<Skeleton className="h-4 w-32" />);
    const el = container.firstElementChild!;
    expect(el.className).toContain("h-4");
    expect(el.className).toContain("w-32");
    expect(el.className).toContain("animate-pulse");
  });

  it("passes through additional props", () => {
    const { container } = render(<Skeleton data-testid="my-skeleton" />);
    const el = container.querySelector('[data-testid="my-skeleton"]');
    expect(el).not.toBeNull();
  });
});
