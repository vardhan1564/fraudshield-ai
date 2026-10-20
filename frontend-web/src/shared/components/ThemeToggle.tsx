import { Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/shared/components/ui/button";
import { useTheme } from "@/shared/hooks/useTheme";

export function ThemeToggle() {
  const { t } = useTranslation();
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? t("common.light") : t("common.dark");
  return (
    <Button variant="outline" size="icon" onClick={toggle} aria-label={`${t("common.theme")}: ${next}`} title={next}>
      {theme === "dark" ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
    </Button>
  );
}
