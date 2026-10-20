import { Hammer } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/shared/components/EmptyState";

/** Placeholder until the page's task builds it (the layout, auth and data layer are ready). */
export function ComingSoonPage({ task }: { task: string }) {
  const { t } = useTranslation();
  return (
    <EmptyState
      icon={Hammer}
      title={t("common.comingSoon")}
      body={t("common.comingSoonBody", { task })}
      className="min-h-[50vh]"
    />
  );
}
