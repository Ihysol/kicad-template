---
title: "ProjectName - Documentation"
author: "Ihysol"
date: "2025-07-08"
fontsize: 11pt
geometry: margin=2.5cm
lang: en
colorlinks: true
header-includes:
    - \usepackage{float}
    - \usepackage{graphicx}
    - \usepackage{tikz}
    - \usepackage{caption}
    - \usepackage{siunitx}
---

```{=latex}
\tableofcontents
\clearpage
```

# Concepts
Brief concept of the project.

## concept-placeholder

```{=latex}
\begin{figure}[H]
\centering
\begin{minipage}[t]{0.48\linewidth}
\includegraphics[width=0.95\linewidth, trim=0cm 0cm 0cm 0cm, clip]{img/TODO.png}
\caption{Main PCB}
\end{minipage}
\end{figure}
```

This describes the first concept. e.g. "this project combines sound, light and an ethernet interface"

...

```{=latex}
\newpage
```
# Hardware

```{=latex}
\begin{figure}[H]
\centering
\begin{minipage}[t]{0.48\linewidth}
  \centering
  \includegraphics[width=0.95\linewidth]{img/TODO.png}
  \caption{PCB front}
\end{minipage}
\hfill
\begin{minipage}[t]{0.48\linewidth}
  \centering
  \includegraphics[width=0.95\linewidth]{img/TODO.png}
  \caption{PCB back}
\end{minipage}
\end{figure}
```

## Functional Description
Introduction in what does what.

## topic1
This describes first hardware topic. e.g. LED driver and LEDs.

```{=latex}
\newpage
```
# Software

## Firmware

## Other software (gui ect)

```{=latex}
\newpage
```
# Tests
Here go tests and results for this project. 

## Test1 
LED Light tests.

### Test Procedure
Measured LEDs running different input currents, PWM, ...

### Result and Solution
LEDs were too dim. Upgraded to new leds


## Test2
LED Temperature testing

### Test Procedure
Measured LED Temperatur over time using varring currents, PWM, ...

### Test Result and Solution
LEDs running to hot. Adjusting switching frequency on LED-Driver and adding heatsinks to LED-PCBs.

...

