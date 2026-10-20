import "i18next";

import type en from "./en.json";

// Translation keys are checked at compile time against en.json (hi/mr must have the same keys).
declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "translation";
    resources: { translation: typeof en };
    returnNull: false;
  }
}
