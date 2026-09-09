import torch
import torch.nn as nn
import numpy as np
import asyncio
import json
import websockets
from typing import Callable, Any, AsyncIterator, Tuple, Dict

# ==============================================================================
# 1. ASYNCHRONER HYLOMORPHISMUS-KERN MIT WEBSOCKET-BROADCAST & GUARD
# ==============================================================================
async def hylo_commercial_websocket_core(
    coalg_gen_factory: Callable[[Any], AsyncIterator[Any]], 
    initial_state: Any, 
    space_id: int,
    websocket: websockets.WebSocketServerProtocol
) -> Any:
    """
    Kommerzieller Hylomorphismus-Kern. Konsumiert den asynchronen Coinduktions-Stream
    und pusht die physikalischen Diagnosedaten in Echtzeit über das Netzwerk-Interface.
    """
    gen = coalg_gen_factory(initial_state)
    
    try:
        async for step_result in gen:
            if isinstance(step_result, dict) and step_result.get("TERMINATE_FOR_HARDWARE_PROTECTION", False):
                # Kritischen Nothalt an den Client senden vor Terminierung
                payload = {
                    "status": "CRITICAL_HALT",
                    "space_id": space_id,
                    "reason": step_result["reason"],
                    "metrics": step_result.get("metrics", {})
                }
                await websocket.send(json.dumps(payload))
                print(f"\n[SaaS-Gateway] Space {space_id} kritisch gestoppt. Payload gesendet.")
                return step_result["last_stable_fields"]
                
            # Erfolgreichen Berechnungsschritt an kommerziellen Client streamen
            if isinstance(step_result, dict) and "step" in step_result:
                payload = {
                    "status": "OPERATIONAL",
                    "space_id": space_id,
                    "step": step_result["step"],
                    "k_star": float(step_result["k_star"]),
                    "C_ratio": float(step_result["C_ratio"]),
                    "energy_density": float(torch.mean(step_result["field"]**2).item())
                }
                await websocket.send(json.dumps(payload))
            
            # Kooperative Kontroll-Abgabe an den asyncio Scheduler
            await asyncio.sleep(0.001) # 1ms Puffer zur Vermeidung von Network Congestion
            
    except websockets.exceptions.ConnectionClosed:
        print(f"[SaaS-Gateway] Verbindung zu Client von Space {space_id} unerwartet abgebrochen.")
        
    return step_result

# ==============================================================================
# 2. HYDRODYNAMISCHE ENGINE MIT SPEKTRAL-PROJEKTION & SOBOLEV-GUARD
# ==============================================================================
class IntegratedNavierStokesAsyncEngine(nn.Module):
    def __init__(self, nu: float, L: float = 2 * np.pi, grid_size: int = 32, dt: float = 0.001):
        super().__init__()
        self.nu = float(nu)
        self.L = float(L)
        self.N = int(grid_size)
        self.dt = float(dt)
        self.C_crit = 1.0 / np.sqrt(3.0)

        dx = self.L / self.N
        k_seq = np.fft.fftfreq(self.N, d=dx) * 2.0 * np.pi
        kx, ky, kz = np.meshgrid(k_seq, k_seq, k_seq, indexing='ij')
        k_abs = np.sqrt(kx**2 + ky**2 + kz**2)
        
        self.register_buffer('k_norm', torch.tensor(k_abs, dtype=torch.float32))

    def evaluate_spectral_bounds(self, u_field: torch.Tensor) -> Tuple[float, float, bool, bool]:
        k_norm = self.k_norm.to(u_field.device)
        u_hat = torch.fft.fftn(u_field, dim=(-3, -2, -1), norm="ortho")
        u_amplitude = torch.norm(u_hat, dim=0)
        
        is_euler_singular = (self.nu == 0.0)
        h_5_2_weight = (1.0 + k_norm**2)**(1.25)
        sobolev_5_2_norm = torch.sqrt(torch.sum(h_5_2_weight * (u_amplitude**2)))
        
        C_ratio = float((sobolev_5_2_norm / self.nu).item()) if self.nu > 0 else float('inf')
        is_bifurcated = C_ratio >= self.C_crit
        
        advection_k = k_norm * (u_amplitude ** 2)
        dissipation_k = self.nu * (k_norm ** 2) * u_amplitude
        spectral_imbalance = advection_k - dissipation_k
        
        valid_mask = k_norm > 0
        violating_k = k_norm[valid_mask & (spectral_imbalance > 0)]
        k_star = float(torch.max(violating_k).item()) if violating_k.numel() > 0 else 0.0
        
        return C_ratio, k_star, is_bifurcated, is_euler_singular

    def apply_galerkin_projection(self, u_field: torch.Tensor, k_star: float) -> torch.Tensor:
        if k_star <= 0.0:
            return u_field
        k_norm = self.k_norm.to(u_field.device)
        u_hat = torch.fft.fftn(u_field, dim=(-3, -2, -1), norm="ortho")
        galerkin_mask = (k_norm <= k_star).unsqueeze(0)
        return torch.fft.ifftn(u_hat * galerkin_mask, dim=(-3, -2, -1), norm="ortho").real

    def execute_spectral_step(self, u_field: torch.Tensor) -> torch.Tensor:
        k_norm = self.k_norm.to(u_field.device)
        viscous_decay = torch.exp(-self.nu * (k_norm**2) * self.dt).unsqueeze(0)
        u_hat = torch.fft.fftn(u_field, dim=(-3, -2, -1), norm="ortho")
        return torch.fft.ifftn(u_hat * viscous_decay, dim=(-3, -2, -1), norm="ortho").real

    def apply_physical_forcing(self, u_field: torch.Tensor, k_force: float = 4.0) -> torch.Tensor:
        k_norm = self.k_norm.to(u_field.device)
        u_hat = torch.fft.fftn(u_field, dim=(-3, -2, -1), norm="ortho")
        forcing_mask = (k_norm <= k_force).unsqueeze(0)
        f_hat = torch.randn_like(u_hat) * 0.18 * forcing_mask
        return torch.fft.ifftn(u_hat + f_hat * self.dt, dim=(-3, -2, -1), norm="ortho").real

    async def spawn_simulation_stream(self, initial_fields: Tuple[int, torch.Tensor]) -> AsyncIterator[Any]:
        timesteps, u = initial_fields
        t = 0
        while t < timesteps:
            C_ratio, k_star, bifurcated, euler_singular = self.evaluate_spectral_bounds(u)
            
            if euler_singular or bifurcated:
                yield {
                    "TERMINATE_FOR_HARDWARE_PROTECTION": True,
                    "reason": f"Sobolev-Bifurkation oder unviskoser Kollaps. C-Ratio: {C_ratio:.4f}",
                    "last_stable_fields": u,
                    "metrics": {"C_ratio": C_ratio, "k_star": k_star}
                }
                return
            
            u_compressed = self.apply_galerkin_projection(u, k_star)
            u_next = self.execute_spectral_step(u_compressed)
            u = self.apply_physical_forcing(u_next, k_force=3.5)
            
            t += 1
            yield {"step": t, "k_star": k_star, "C_ratio": C_ratio, "field": u}

# ==============================================================================
# 3. WEBSOCKET-SERVER INTERFACE (PRODUKTIONS-ROUTE)
# ==============================================================================
class CommercialPhysicsGatewayServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self.host = host
        self.port = port
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"[SaaS-Init] Backend läuft auf Hardware: {self.device}")

    async def handle_client(self, websocket: websockets.WebSocketServerProtocol):
        """ Handles incoming commercial connection streams. """
        print(f"[Gateway] Neuer API-Inhaber über WebSocket verbunden.")
        
        try:
            # 1. Empfange die Konfigurations-Initialisierung vom Client (JSON)
            config_msg = await websocket.recv()
            config = json.loads(config_msg)
            
            grid_size = config.get("grid_size", 32)
            nu = config.get("viscosity", 0.005)
            timesteps = config.get("timesteps", 100)
            space_id = config.get("space_id", 99)
            
            print(f"[Gateway] Starte dedizierten Vektorraum {space_id} (nu={nu}, N={grid_size})")
            
            # 2. Instanziierung der exakten Triebwerks-Klasse im RAM
            engine = IntegratedNavierStokesAsyncEngine(nu=nu, grid_size=grid_size).to(self.device)
            
            # Synthetisches Startfeld generieren (In der Praxis vom Client via Bytes gesendet)
            sample_u = torch.randn(3, grid_size, grid_size, grid_size, device=self.device) * 0.05
            initial_state = (timesteps, sample_u)
            
            # 3. Einkopplung in den asynchronen Hylomorphismus-Kern
            await hylo_commercial_websocket_core(
                engine.spawn_simulation_stream,
                initial_state,
                space_id=space_id,
                websocket=websocket
            )
            
        except json.JSONDecodeError:
            await websocket.close(code=4000, reason="Invalid JSON Config Structure")
        except Exception as e:
            print(f"[Gateway-Error] Interner Fehler: {str(e)}")
            await websocket.close(code=4001, reason=str(e))

    def start_server(self):
        """ Startet den permanenten asynchronen Server-Loop. """
        start_server = websockets.serve(self.handle_client, self.host, self.port)
        print(f"🚀 Commercial Physics Gateway aktiv auf ws://{self.host}:{self.port}")
        asyncio.get_event_loop().run_until_complete(start_server)
        asyncio.get_event_loop().run_forever()

if __name__ == "__main__":
    # Start des kommerziellen Endpunkts
    gateway = CommercialPhysicsGatewayServer(host="127.0.0.1", port=8765)
    gateway.start_server()
