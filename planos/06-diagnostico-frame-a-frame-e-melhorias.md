# Plano 06 — Diagnóstico de Análise Frame a Frame & Refinamento Semântico

> **Baseado em evidência empírica:** Análise frame a frame e inspeção de metadados reais de vídeos gerados pelo MoneyPrinterTurbo (Shorts e Vídeos Longos reais do canal *Debugando o Mundo*).

---

## 1. Diagnóstico do Vídeo Short Real (O Bug do F-22)

- **Duração:** 36.4s | **Aspect Ratio:** 9:16 vertical | **Cortes:** 5s por clipe.
- **Linha do tempo da fala vs. O que apareceu na tela:**

| Timestamp da Narração | Texto Narrado na Legenda | O que DEVERIA aparecer | O que a busca ingênua colocou | Desconexão / Problema |
|---|---|---|---|---|
| `00:00 - 00:05` | *"Em 2007, 12 caças F-22 decolaram do Havaí rumo ao Japão..."* | Caças militares decolando / caça furtivo F-22 | Homem sentado em frente a computador escuro (`dark digital server room`) | **Grave:** Abre o vídeo falando de caças no céu mostrando um cara num escritório/servidor. |
| `00:05 - 00:10` | *"Cada avião custava mais de 150 milhões de dólares e era o mais avançado..."* | Caça militar F-22 em voo espetacular | Caça militar em show aéreo | **Adequado por coincidência**, mas o corte de 5s corta abruptamente no meio da frase. |
| `00:10 - 00:15` | *"Mas ao cruzarem a Linha Internacional de Data, o impensável aconteceu..."* | Globo terrestre / mapa da Linha de Data / Oceano Pacífico | Cockpit de avião comercial | **Fraco:** Deveria ilustrar a transição geográfica ou o mapa. |
| `00:15 - 00:23` | *"todos os computadores travaram simultaneamente. Sem GPS, sem telas..."* | Telas de radar piscando erro / glitch digital | Homem com máscara de hacker conversando | **AI Slop / Cringe:** Hacker genérico com máscara nada tem a ver com falha de sistema militar. |
| `00:23 - 00:32` | *"O motivo? Um bug no código que não sabia calcular a mudança brusca de data..."* | Código fonte com bug / calendário digital / avião-tanque | Janela de avião de passageiro comercial com nuvens | **Desconexo:** Fala de bug e avião-tanque militar, mostra viagem de férias na janela do avião comercial. |

---

## 2. Diagnóstico do Vídeo Longo Real (Stanislav Petrov - 1983)

- **Duração:** 1m28s (normalizado -14 LUFS) | **Aspect Ratio:** 16:9 widescreen | **Cortes:** 6s por clipe.
- **Problemas identificados:**
  1. **Anacronismo Visual Gritante:** Quando a narração diz *"Pelo protocolo militar estrito, o tenente-coronel Stanislav Petrov..."*, a busca por `military general in uniform` baixou e colocou soldados da **Revolução Francesa do século XVIII** marchando com mosquetes para ilustrar a **Guerra Fria de 1983**.
  2. **Imprecisão Temática:** Quando fala de satélites no espaço detectando mísseis, colocou usinas nucleares modernas em visão aérea por causa do termo `nuclear explosion archive`.

---

## 3. Melhorias e Regras Críticas Adicionadas aos Planos

Para resolver os gargalos evidenciados na análise frame a frame, os seguintes requisitos foram incorporados à especificação técnica:

### 3.1 Filtro de Contexto Histórico e Época (Anti-Anacronismo)
- O LLM gerador de prompts visuais (`app/services/llm.py`) deve receber a **Era/Época** do vídeo (ex: `1980s Cold War`, `Modern Military 2000s`) e anexar termos de época ou restrições negativas (ex: `avoid: 18th century, muskets, civil war`).

### 3.2 Blacklist Semântica para Clichês Tóxicos (Anti-Hacker Mask / Anti-Stock Slop)
- Banir termos genéricos de stock que trazem atores com máscaras plásticas de "Anonymous/Hacker", pessoas segurando lupas em frente a monitores ou computadores genéricos quando o contexto for de aviação ou sistemas aeroespaciais.

### 3.3 Sincronização por "Beats Narrativos" e não por Tempo Fixo
- Não usar `video_clip_duration = 5s` cego. O corte do vídeo **deve coincidir exatamente com a pontuação e troca de sujeito da fala** (definido pelos timestamps das palavras no Whisper). Se uma frase dura 3.8s, o clipe dura exatamente 3.8s.

### 3.4 Fallback de Hierarquia Semântica (3 Níveis)
1. **Nível 1 (Específico da Cena):** Termo direto (ex: `F-22 fighter jet flight`).
2. **Nível 2 (Conceitual / Contextual):** Termo do ambiente da cena (ex: `military aircraft cockpit instruments`).
3. **Nível 3 (Tema Geral do Vídeo):** Termo macro (ex: `military aviation high speed`).
- **Nunca** recorrer a termos aleatórios sem ligação com a cena.
