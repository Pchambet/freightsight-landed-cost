import { enUS, frFR } from "@clerk/localizations";

/**
 * `frFR` leaves some keys undefined, and Clerk falls back to English for each of them: a French
 * tester signing up read "Create a password", "Edit email address", and the header's org switcher was
 * announced as "Open organization switcher". These are the gaps on the screens a customer goes
 * through (sign-in, sign-up, choosing the organization, the header); billing, SSO and MFA screens are
 * not used. The application name in "pour continuer vers …" is set in the Clerk dashboard, not here.
 */
const fr = {
  ...frFR,
  formFieldInputPlaceholder__signUpPassword: "Choisissez un mot de passe",
  formFieldInput__emailAddress_format: "Format attendu : nom@example.com",
  identityPreviewEditButton__emailAddress: "Modifier l'adresse e-mail",
  identityPreviewEditButton__identifier: "Modifier l'identifiant",
  identityPreviewEditButton__phoneNumber: "Modifier le numéro de téléphone",
  organizationSwitcher: {
    ...frFR.organizationSwitcher,
    action__openOrganizationSwitcher: "Ouvrir le sélecteur d'organisation",
    action__closeOrganizationSwitcher: "Fermer le sélecteur d'organisation",
  },
  signIn: {
    ...frFR.signIn,
    passwordCompromised: { ...frFR.signIn?.passwordCompromised, title: "Mot de passe compromis" },
    passwordUntrusted: { ...frFR.signIn?.passwordUntrusted, title: "Mot de passe peu sûr" },
    protectCheck: {
      title: "Vérification de votre demande",
      subtitle: "Merci de patienter pendant que nous vérifions votre demande.",
      loading: "Chargement…",
      retryButton: "Réessayer",
    },
  },
  signUp: {
    ...frFR.signUp,
    protectCheck: {
      title: "Vérification de votre demande",
      subtitle: "Merci de patienter pendant que nous vérifions votre demande.",
      loading: "Chargement…",
      retryButton: "Réessayer",
    },
  },
  taskChooseOrganization: {
    ...frFR.taskChooseOrganization,
    alerts: {
      organizationAlreadyExists: "Une organisation existe déjà pour le nom de société détecté ({{organizationName}}) et {{organizationDomain}}. Rejoignez-la sur invitation.",
    },
  },
  unstable__errors: {
    ...frFR.unstable__errors,
    form_password_matches_identifier: "Le mot de passe ne peut pas reprendre votre adresse e-mail, votre numéro de téléphone ou votre identifiant. Choisissez-en un autre.",
    form_password_untrusted__sign_in: "Votre mot de passe a peut-être été compromis. Pour protéger votre compte, connectez-vous par un autre moyen ; il vous sera demandé de le réinitialiser ensuite.",
  },
} satisfies typeof frFR;

/** Clerk's own screens in the application's language. */
export function clerkLocalization(locale: string): typeof frFR {
  return locale === "fr" ? fr : enUS;
}
