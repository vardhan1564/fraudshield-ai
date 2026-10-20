import { Hammer } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/shared/components/EmptyState";

/** `inset`: already inside a padded page (no extra padding). */
export function CustomerComingSoon({ task, title, inset = false }: { task: string; title?: string; inset?: boolean }) {
  const { t } = useTranslation();
  return (
    <div className={inset ? undefined : "px-5 py-4"}>
      {title && <h1 className="mb-4 text-2xl font-semibold tracking-tight">{title}</h1>}
      <EmptyState icon={Hammer} title={t("common.comingSoon")} body={t("common.comingSoonBody", { task })} />
    </div>
  );
}
