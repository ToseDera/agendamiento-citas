---
name: Clinical Clarity
colors:
  surface: '#f9f9ff'
  surface-dim: '#cfdaf1'
  surface-bright: '#f9f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f0f3ff'
  surface-container: '#e7eeff'
  surface-container-high: '#dee8ff'
  surface-container-highest: '#d8e3fa'
  on-surface: '#111c2c'
  on-surface-variant: '#424752'
  inverse-surface: '#263142'
  inverse-on-surface: '#ebf1ff'
  outline: '#727783'
  outline-variant: '#c2c6d4'
  surface-tint: '#005db6'
  primary: '#00478d'
  on-primary: '#ffffff'
  primary-container: '#005eb8'
  on-primary-container: '#c8daff'
  inverse-primary: '#a9c7ff'
  secondary: '#006a64'
  on-secondary: '#ffffff'
  secondary-container: '#77f4e8'
  on-secondary-container: '#006f68'
  tertiary: '#43484c'
  on-tertiary: '#ffffff'
  tertiary-container: '#5b6063'
  on-tertiary-container: '#d6dade'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d6e3ff'
  primary-fixed-dim: '#a9c7ff'
  on-primary-fixed: '#001b3d'
  on-primary-fixed-variant: '#00468c'
  secondary-fixed: '#7af6eb'
  secondary-fixed-dim: '#5adacf'
  on-secondary-fixed: '#00201d'
  on-secondary-fixed-variant: '#00504b'
  tertiary-fixed: '#dfe3e7'
  tertiary-fixed-dim: '#c3c7cb'
  on-tertiary-fixed: '#171c1f'
  on-tertiary-fixed-variant: '#43474b'
  background: '#f9f9ff'
  on-background: '#111c2c'
  surface-variant: '#d8e3fa'
typography:
  display-lg:
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
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  unit: 4px
  container-max: 1280px
  gutter: 24px
  margin-mobile: 16px
  margin-desktop: 40px
  stack-sm: 8px
  stack-md: 16px
  stack-lg: 32px
---

## Brand & Style
The brand personality focuses on **Reliability, Compassion, and Efficiency**. The design system is engineered to reduce the cognitive load and anxiety often associated with medical scheduling. The target audience is diverse, ranging from tech-savvy young adults to seniors requiring high legibility and clear affordances.

The design style is **Modern Minimalism with a Humanist touch**. It utilizes generous whitespace to create a "breathable" interface, ensuring that critical information—such as appointment times and provider names—is never obscured by visual noise. The aesthetic is clinical but not cold, professional but not intimidating, achieving a balance that evokes an immediate emotional response of calm and trust.

## Colors
The color palette is rooted in medical tradition but updated for digital clarity. 
- **Primary Blue (#005EB8):** Used for primary actions, navigation headers, and critical brand moments. It conveys authority and stability.
- **Secondary Teal (#00A79D):** Used for success states, secondary accents, and positive reinforcement (e.g., "Available" slots).
- **Neutral Greys:** A range of cool-toned greys handles text hierarchy and borders.
- **Surface Tints:** Soft off-whites and pale blues are used to differentiate content areas without relying on harsh lines.

Color contrast ratios must adhere to WCAG AA standards to ensure accessibility for users with visual impairments.

## Typography
This design system utilizes **Inter** for its exceptional legibility and systematic weight distribution. The typography scale is intentionally generous to accommodate older patients. 

- **Headlines:** Use SemiBold (600) to provide strong structural anchors.
- **Body Text:** Use Regular (400) for high readability in long-form content or instructions.
- **Labels:** Use Medium (500) for form fields and UI metadata.
- **Tracking:** Tightened slightly for large headlines to maintain visual density; standard for body text to ensure character recognition.

## Layout & Spacing
The layout follows a **Fluid Grid** logic with fixed maximum widths for desktop to prevent line lengths from becoming unreadable.
- **Desktop (12-column):** Uses a 24px gutter. Content is centered with a 1280px max-width.
- **Tablet (8-column):** Adjusts margins to 24px.
- **Mobile (4-column):** Uses 16px margins. 

The vertical rhythm is based on a **4px baseline grid**. Components should generally use 16px or 24px padding internally to maintain the "clean and open" feel. Dense information (like a calendar grid) may drop to 8px or 12px internal spacing.

## Elevation & Depth
Elevation is handled through **Ambient Shadows** and **Tonal Layers** rather than heavy borders.
- **Level 0 (Base):** Background color (#F8FAFC), used for the main canvas.
- **Level 1 (Cards):** Pure white surfaces with a very soft, diffused shadow (0px 4px 20px rgba(0, 0, 0, 0.05)). This is the primary container for content.
- **Level 2 (Dropdowns/Modals):** Pure white with a more pronounced shadow (0px 10px 30px rgba(0, 0, 0, 0.1)) to indicate a change in the Z-axis.
- **Active States:** Subtle inset shadows or 1px strokes in the primary color indicate focus or selection.

## Shapes
The shape language uses **Rounded (8px-12px)** corners to soften the clinical nature of the application and make the UI feel approachable. 
- **Standard Components:** 8px radius (Buttons, Input Fields).
- **Cards & Containers:** 12px radius to establish a clear containment area.
- **Chips & Tags:** 100px (Pill-shaped) to distinguish them from interactive buttons.

## Components
- **Buttons:** Primary buttons use a solid #005EB8 fill with white text. Secondary buttons use a 1px stroke of the primary color. Hover states should involve a subtle darken (5-10%) of the fill.
- **Input Fields:** Use an 8px radius with a light grey border (#E2E8F0). On focus, the border changes to the primary blue with a 2px outer "glow" (low opacity blue).
- **Cards:** White background, 12px radius, and Level 1 elevation. Used for doctor profiles and appointment summaries.
- **Chips (Availability):** Time slots are represented as high-contrast chips. Available slots use a soft teal background; selected slots use the primary blue; unavailable slots are greyed out with a diagonal strike-through pattern.
- **Lists:** Use horizontal dividers (1px, #EDF2F7) with 16px vertical padding between items for clarity.
- **Checkboxes/Radios:** Large hit targets (min 44x44px) to ensure ease of use for patients with limited motor precision.
- **Progress Indicators:** A thin, secondary teal bar at the top of the booking flow to provide a sense of momentum and completion.