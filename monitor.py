import sys

def controlla_prezzo():
    # Per ora simuliamo che lo script trovi un prezzo di 650€ per testare se l'allerta funziona
    # Successivamente qui metterai il codice che legge i prezzi reali
    prezzo_minimo = 650 
    
    return prezzo_minimo

if __name__ == "__main__":
    prezzo = controlla_prezzo()
    # Stampiamo solo il numero intero, che verrà letto da GitHub
    print(prezzo)
