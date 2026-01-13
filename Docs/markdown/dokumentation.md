---
title: ""
subtitle: ""
author: ""
date: ""
fontsize: 11pt
geometry: margin=2.5cm
lang: de
colorlinks: true
header-includes:
  - \usepackage{float}
  - \usepackage{graphicx}
  - \usepackage{tikz}
  - \usepackage{caption}
  - \usepackage{siunitx}
  - \usepackage{fancyhdr}
  - \pagestyle{fancy}
  - \fancyhf{}
  - \fancyhead[R]{\includegraphics[height=1cm]{img/TODO.png}}
  - \fancyfoot[C]{\thepage}
---

```{=latex}
\begin{titlepage}
\begin{center}
\includegraphics[height=3cm]{img/TODO.png}

\vspace{2cm}
{\Huge <Project Name> \par}
\vspace{0.5cm}
{\Large Documentation \par}
\vspace{1cm}
{\large <Organisation Name> \par}
\vspace{0.5cm}
{\large 2026-01-12 \par}

\vspace{1.5cm}
\includegraphics[width=0.75\linewidth]{img/TODO.png}
\par\smallskip
\textbf{Abbildung 1:} Caption 1

\noindent
\begin{minipage}[t]{0.19\textwidth}
  Contact Info:
\end{minipage}%
\begin{minipage}[t]{0.40\textwidth}
  Your Organisation Name\\
  Adress\\
  Place
\end{minipage}%
\begin{minipage}[t]{0.40\textwidth}
  Other Organisation Name \\
  Adress\\
  Place
\end{minipage}

\end{center}
\end{titlepage}
```

```{=latex}
\newpage
```

```{=latex}
\tableofcontents
\clearpage
```

# Introduction and requirements

```{=latex}
\newpage
```

# Concept phases

## concept 1
text

## concept 2
text

## concept 3
text 

```{=latex}
\newpage
```

# final state of development
intro

## Final concept
text

## Hardware
text

## Software
text

## Tests


```{=latex}
\newpage
```

# Development process and insights

intro about

## Concept1 name and short title
brief concept description

### Concept explanation
more detail

### Hardware
text

### Tests
tests

### Final conclusions

```{=latex}
\newpage
```

## Concept2 name and short title
brief concept description

### Concept explanation
more detail

### Hardware
text

### Tests
tests

### Final conclusions

```{=latex}
\newpage
```

## Concept3 name and short title
brief concept description

### Concept explanation
more detail

### Hardware
text

### Tests
tests

### Final conclusions


```{=latex}
\newpage
```

## Concept4 name and short title
brief concept description

### Concept explanation
more detail

### Hardware
text

### Tests
tests

### Final conclusions

```{=latex}
\newpage
```

# Alternative concepts and additions

```{=latex}
\newpage
```

# templates
```{=latex}
\begin{figure}[H]
\centering
\begin{minipage}[t]{0.48\linewidth}
  \centering
  \includegraphics[width=0.95\linewidth]{img/TODO.png}
  \caption{Einzelpanel Lichtseite (Front)}
\end{minipage}
\hfill
\begin{minipage}[t]{0.48\linewidth}
  \centering
  \includegraphics[width=0.95\linewidth]{img/TODO.png}
  \caption{Einzelpanel Rückseite}
\end{minipage}
\end{figure}
```
```{=latex}
\begin{figure}[H]
\centering
\begin{minipage}[t]{0.48\linewidth}
text  (Abbildung \ref{fig:proto1_lichtbild}).
\end{minipage}
\hfill
\begin{minipage}[t]{0.48\linewidth}
\vspace{0pt} % wichtig für vertikale Ausrichtung
\centering
\includegraphics[width=0.75\linewidth]{img/TODO.png}
\captionof{figure}{figure caption}
\label{fig:figure label}
\end{minipage}
\end{figure}
```