import i18n from "i18next"
import { initReactI18next } from "react-i18next"
import en from "@/locales/en.json"
import { termVariables } from "./terminology"

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en } },
  lng: "en",
  fallbackLng: "en",
  interpolation: { escapeValue: false, defaultVariables: termVariables() },
})

export default i18n
