import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Vendored, not authored here: shadcn and AI Elements components are
    // copied in by their CLI and re-copied on update. Linting them means
    // either editing upstream code we want to keep re-pullable, or living
    // with permanent errors that hide our own.
    "components/ai-elements/**",
  ]),
]);

export default eslintConfig;
