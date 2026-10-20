import { RotateCw, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/shared/components/EmptyState";
import { Button } from "@/shared/components/ui/button";

/** A failed load: what happened in plain words + Try again (spec 06 §2: every list has one). */
export function ErrorState({ title, message, onRetry, className }: {
  title: string;
  message?: string;
  onRetry: () => void;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <EmptyState
      icon={TriangleAlert}
      title={title}
      body={message}
      className={className}
      action={
        <Button variant="outline" onClick={onRetry}>
          <RotateCw aria-hidden="true" />
          {t("common.retry")}
        </Button>
      }
    />
  );
}
