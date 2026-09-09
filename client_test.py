# client_test.py
import asyncio
import json
import websockets

async def run_client_simulation(viscosity: float, space_id: int):
    uri = "ws://localhost:8765"
    print(f"[Client] Verbinde mit SaaS-Gateway unter {uri}...")
    
    try:
        async with websockets.connect(uri) as websocket:
            # 1. Sende die mathematische Konfiguration an den Server
            config = {
                "grid_size": 32,
                "viscosity": viscosity,
                "timesteps": 50,
                "space_id": space_id
            }
            print(f"[Client-{space_id}] Sende Konfiguration: nu={viscosity}")
            await websocket.send(json.dumps(config))
            
            # 2. Konsumiere den asynchronen Telemetrie-Datenstrom
            async for message in websocket:
                data = json.loads(message)
                status = data.get("status")
                
                if status == "OPERATIONAL":
                    print(f"[Client-{space_id}] Schritt {data['step']}: "
                          f"k* = {data['k_star']:.2f} | "
                          f"C-Ratio = {data['C_ratio']:.4f} | "
                          f"Energie = {data['energy_density']:.6f}")
                    
                elif status == "CRITICAL_HALT":
                    print(f"\n[Client-{space_id}] !!! KRITISCHER NOTHALT VOM SERVER EMPFANGEN !!!")
                    print(f"Grund: {data['reason']}")
                    print(f"Metriken beim Kollaps: {data['metrics']}")
                    break
                    
    except ConnectionRefusedError:
        print("[Client] Fehler: Der WebSocket-Server läuft nicht. Bitte starten Sie zuerst den Server.")
    except Exception as e:
        print(f"[Client] Ausnahme abgefangen: {str(e)}")

async def main():
    print("=== START DER ASYNCHRONEN CLIENT-VERIFIKATION ===")
    
    # Task 1: Stabile Strömung (Hohe Viskosität fängt die Kaskade ein)
    task_stable = run_client_simulation(viscosity=0.05, space_id=1)
    
    # Task 2: Instabile Strömung (Niedrige Viskosität löst die Sobolev-Bifurkation aus)
    task_unstable = run_client_simulation(viscosity=0.002, space_id=2)
    
    # Starte beide Client-Simulationen parallel auf dem Event-Loop
    await asyncio.gather(task_stable, task_unstable)

if __name__ == "__main__":
    asyncio.run(main())




