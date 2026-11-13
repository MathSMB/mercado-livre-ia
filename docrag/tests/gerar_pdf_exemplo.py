"""Gera tests/dados/pdc-exemplo.pdf (documento fictício). Uso: python tests/gerar_pdf_exemplo.py tests/dados/pdc-exemplo.pdf
Precisa de reportlab (só para regenerar; os testes usam o PDF já gerado)."""
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
import sys
st = getSampleStyleSheet()
doc = SimpleDocTemplate(sys.argv[1], pagesize=A4)
def rodape(c, d):
    c.setFont("Helvetica", 8); c.drawString(40, 20, f"Procedimento de Comercialização exemplo - Página {d.page}")
E = []
E += [Paragraph("Procedimento de Comercialização", st["Title"]), Paragraph("Submódulo 9.9 – Exemplo de Teste", st["Heading2"]),
      Paragraph("Versão 2.0 – Vigência: 01/01/2026", st["Normal"]), Paragraph("Aprovado pela REN ANEEL nº 1.234/2025", st["Normal"])]
E += [Paragraph("1. OBJETIVO", st["Heading1"]), Paragraph("Este submódulo estabelece as regras de exemplo para o Mecanismo de Compensação de Sobras e Déficits (MCSD) e para a Câmara de Comercialização de Energia Elétrica (CCEE). " * 3, st["Normal"])]
E += [Paragraph("2. PREMISSAS", st["Heading1"]), Paragraph("2.1 Prazos", st["Heading2"]),
      Paragraph("O agente deve enviar os dados até o 5º dia útil do mês subsequente, com tolerância de 2,5% sobre o montante de 1.250,75 MWh. " * 4, st["Normal"]),
      Paragraph("2.2 Responsabilidades", st["Heading2"]), Paragraph("Cabe à CCEE publicar o resultado em até 10 dias. " * 5, st["Normal"])]
E += [PageBreak(), Paragraph("3. ATIVIDADES", st["Heading1"]), Paragraph("A tabela a seguir resume as atividades e prazos.", st["Normal"]),
      Table([["Atividade", "Responsável", "Prazo"], ["Enviar dados", "Agente", "MS+5du"], ["Validar", "CCEE", "MS+8du"], ["Publicar", "CCEE", "MS+10du"]], style=[("GRID",(0,0),(-1,-1),0.5,"black")]),
      Paragraph("3.1 Cálculo", st["Heading2"]), Paragraph("O montante é dado por VTOT = Σ (G × FA) para cada perfil a no mês m.", st["Normal"]),
      Paragraph("4. GLOSSÁRIO", st["Heading1"]),
      Table([["Variável", "Descrição"], ["VTOT", "Valor total do agente no mês"], ["FA", "Fator de ajuste"]], style=[("GRID",(0,0),(-1,-1),0.5,"black")])]
doc.build(E, onFirstPage=rodape, onLaterPages=rodape)
