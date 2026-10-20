import { Compass } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { EmptyState } from "@/shared/components/EmptyState";
import { buttonVariants } from "@/shared/components/ui/buttonVariants";

export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <div className="grid min-h-dvh place-items-center p-6">
      <EmptyState
        icon={Compass}
        title={t("notFound.title")}
        body={t("notFound.body")}
        className="w-full max-w-md"
        action={
          <Link to="/" className={buttonVariants()}>
            {t("notFound.home")}
          </Link>
        }
      />
    </div>
  );
}
