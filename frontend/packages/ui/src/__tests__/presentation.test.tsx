import { Circle } from "lucide-react";
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { Badge } from "../components/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/card";
import { EmptyState } from "../components/empty-state";
import { Input } from "../components/input";
import { PageContainer } from "../components/page-container";
import { PageHeader } from "../components/page-header";
import { Separator } from "../components/separator";
import { Skeleton } from "../components/skeleton";
import { Spinner } from "../components/spinner";

it("renders cards, badges, and inputs with caller styling", () => {
  render(
    <Card className="card-extra">
      <CardHeader>
        <CardTitle>Printer</CardTitle>
        <CardDescription>Ready</CardDescription>
      </CardHeader>
      <CardContent>
        <Badge variant="success">Online</Badge>
        <Input aria-label="Name" className="input-extra" />
      </CardContent>
    </Card>,
  );
  expect(screen.getByText("Printer").closest(".card-extra")).toBeInTheDocument();
  expect(screen.getByText("Online")).toHaveClass("bg-success");
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveClass("input-extra");
});

it("renders every optional empty-state region", () => {
  render(
    <EmptyState
      icon={Circle}
      title="No models"
      description="Upload the first one."
      action={<button>Upload</button>}
      className="empty-extra"
    />,
  );
  expect(screen.getByText("No models").closest(".empty-extra")).toBeInTheDocument();
  expect(screen.getByText("Upload the first one.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument();
});

it("selects page widths and renders optional header regions", () => {
  render(
    <PageContainer width="prose" className="page-extra">
      <PageHeader title="Settings" description="Configure" actions={<button>Save</button>} />
    </PageContainer>,
  );
  expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
  expect(screen.getByText("Configure")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  expect(screen.getByText("Configure").closest(".page-extra")).toHaveClass("max-w-4xl");
});

it("exposes semantic separators, loading status, and skeleton attributes", () => {
  render(
    <>
      <Separator decorative={false} orientation="vertical" />
      <Spinner size="lg" label="Loading models" className="spinner-extra" />
      <Skeleton data-testid="skeleton" className="skeleton-extra" />
    </>,
  );
  expect(screen.getByRole("separator")).toHaveAttribute("aria-orientation", "vertical");
  expect(screen.getByRole("status", { name: "Loading models" })).toHaveClass(
    "h-6",
    "spinner-extra",
  );
  expect(screen.getByTestId("skeleton")).toHaveClass("animate-pulse", "skeleton-extra");
});
