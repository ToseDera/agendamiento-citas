---
name: Serene Clinical
colors:
  surface: '#faf8ff'
  surface-dim: '#cdd9ff'
  surface-bright: '#faf8ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f2f3ff'
  surface-container: '#e9edff'
  surface-container-high: '#e1e8ff'
  surface-container-highest: '#d9e2ff'
  on-surface: '#081a3d'
  on-surface-variant: '#424752'
  inverse-surface: '#202f53'
  inverse-on-surface: '#edf0ff'
  outline: '#727783'
  outline-variant: '#c2c6d4'
  surface-tint: '#055db6'
  primary: '#005bb2'
  on-primary: '#ffffff'
  primary-container: '#3174ce'
  on-primary-container: '#fefcff'
  inverse-primary: '#a9c7ff'
  secondary: '#236296'
  on-secondary: '#ffffff'
  secondary-container: '#8fc5ff'
  on-secondary-container: '#055285'
  tertiary: '#4a5e6e'
  on-tertiary: '#ffffff'
  tertiary-container: '#627788'
  on-tertiary-container: '#fcfcff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d6e3ff'
  primary-fixed-dim: '#a9c7ff'
  on-primary-fixed: '#001b3d'
  on-primary-fixed-variant: '#00468c'
  secondary-fixed: '#d0e4ff'
  secondary-fixed-dim: '#9bcbff'
  on-secondary-fixed: '#001d34'
  on-secondary-fixed-variant: '#004a79'
  tertiary-fixed: '#cfe5f8'
  tertiary-fixed-dim: '#b4c9dc'
  on-tertiary-fixed: '#071e2b'
  on-tertiary-fixed-variant: '#354958'
  background: '#faf8ff'
  on-background: '#081a3d'
  surface-variant: '#d9e2ff'
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.01em
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 8px
  xs: 4px
  sm: 12px
  md: 24px
  lg: 48px
  xl: 80px
  gutter: 24px
  margin-mobile: 16px
  margin-desktop: 64px
---

## Brand & Style

The design system is centered on the principles of **Modern Clinical Order**. It prioritizes patient reassurance and professional clarity through a high-fidelity execution of contemporary SaaS aesthetics blended with medical reliability. 

The brand personality is empathetic yet authoritative. It leverages **Minimalism** to reduce cognitive load for users who may be in stressful health situations, while employing **Corporate Modern** structures to ensure the interface feels systematic and safe. The visual narrative relies on "breathable" interfaces where whitespace acts as a functional tool to separate complex medical data, evoking a sense of calm and precision.

## Colors

The palette utilizes a monochromatic blue foundation to establish trust and stability. 

- **Primary (#3A7BD5):** Used for critical actions, active states, and brand-defining moments. 
- **Secondary & Tertiary (#8EC5FF, #D6ECFF):** These shades function as structural anchors for surfaces, hover states, and iconography backgrounds, creating a soft "watery" depth.
- **Neutral (#1D2D50):** A deep navy used for typography and high-contrast borders to ensure maximum legibility and an authoritative tone.
- **Background (#F6FBFF):** A tinted off-white that prevents screen glare and feels more welcoming than pure clinical white.

## Typography

This design system uses **Inter** exclusively to lean into its utilitarian, highly legible nature. The typographic scale is generous, with increased line heights to aid readability of medical results and instructions. 

Headlines use semi-bold and bold weights with slight negative letter-spacing to appear "tight" and professional. Labels and captions utilize medium weights to maintain hierarchy without needing excessive color changes.

## Layout & Spacing

The layout follows a **Fluid Grid** model based on an 8px root unit. 

- **Desktop:** 12-column grid with 24px gutters. Content is typically contained in wide-margin central columns to maintain focus.
- **Mobile:** 4-column grid with 16px margins. 
- **Rhythm:** Generous vertical spacing (`xl` units) is used between major sections to prevent the UI from feeling cluttered or "urgent." All card containers should use `md` (24px) internal padding as a minimum standard.

## Elevation & Depth

Hierarchy is established through **Tonal Layering** and **Ambient Shadows**. Surfaces do not "float" aggressively; instead, they sit just above the background to maintain a grounded, stable feel.

- **Level 1 (Cards/Inputs):** Uses a very soft, diffused shadow (0px 4px 20px) with a 5% opacity of the Neutral color.
- **Level 2 (Modals/Dropdowns):** A more defined shadow (0px 12px 32px) with 10% opacity.
- **Interactions:** Subtle inner shadows or 1px strokes in the Accent color (#8EC5FF) are preferred over heavy drop shadows for active states.

## Shapes

The shape language is **Rounded**, moving away from sharp clinical edges toward a more "human" and approachable feel. 

- **Standard Elements:** Buttons, input fields, and small cards use a 0.5rem (8px) radius.
- **Large Containers:** Main content areas and large feature cards use a 1rem (16px) radius.
- **Status Indicators:** Use fully pill-shaped (capsule) geometry for tags and badges to clearly distinguish them from interactive buttons.

## Components

- **Buttons:** Primary buttons are solid #3A7BD5 with white text. Secondary buttons use #D6ECFF background with #1D2D50 text. Use subtle transitions on hover.
- **Input Fields:** Large 56px height for accessibility. Background is pure white with a 1px #D6ECFF border. On focus, the border shifts to #3A7BD5 with a soft glow.
- **Cards:** White or #F6FBFF background with a 1px #D6ECFF border. Ensure padding is never less than 24px.
- **Chips/Badges:** Pill-shaped. Use #D6ECFF backgrounds for neutral info and soft greens/reds for medical status updates (e.g., "Confirmed", "Pending").
- **Lists:** Clean rows separated by 1px #D6ECFF dividers. Avoid heavy boxes around every list item; use whitespace to define rows.
- **Medical Specifics:** Prescription cards and appointment slots should include prominent icons from a consistent, soft-edged stroke library.