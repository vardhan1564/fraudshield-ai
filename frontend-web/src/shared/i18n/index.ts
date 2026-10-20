import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import type { Language } from "@/shared/types/api";

import en from "./en.json";
import hi from "./hi.json";
import mr from "./mr.json";

export const LANGUAGES: { code: Language; label: string }[] = [
  { code: "en", label: "English" },
  { code: "hi", label: "हिन्दी" },
  { code: "mr", label: "मराठी" },
];

const STORAGE_KEY = "fraudshield.language";

function isLanguage(value: string | null): value is Language {
  return value === "en" || value === "hi" || value === "mr";
}

function storedLanguage(): Language {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return isLanguage(value) ? value : "en";
  } catch {
    return "en";
  }
}

/** Switch language and remember it (the profile language wins after login). */
export function setLanguage(language: Language): void {
  void i18n.changeLanguage(language);
  document.documentElement.lang = language;
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // storage blocked: keep it for this visit only
  }
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, hi: { translation: hi }, mr: { translation: mr } },
  lng: storedLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false }, // React escapes already
  returnNull: false,
});

document.documentElement.lang = i18n.language;

export default i18n;
