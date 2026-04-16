#!/usr/bin/env bash
# =============================================================================
# setup_acceso_remoto.sh — Configuracion segura de acceso remoto a Linux
#
# Dos modos:
#   1) Tailscale (recomendado) — VPN mesh, sin abrir puertos en el router
#   2) SSH hardened            — Puerto SSH expuesto pero blindado
#
# Uso:  sudo ./setup_acceso_remoto.sh [--tailscale | --ssh | --ambos]
# =============================================================================
set -euo pipefail

# ── Colores ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Funciones auxiliares ─────────────────────────────────────────────────────
info()  { printf "${CYAN}[INFO]${NC}  %s\n" "$*"; }
ok()    { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
error() { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "Este script necesita permisos de root. Ejecuta: sudo $0"
        exit 1
    fi
}

detect_distro() {
    if command -v apt-get &>/dev/null; then
        PKG_MANAGER="apt"
    elif command -v dnf &>/dev/null; then
        PKG_MANAGER="dnf"
    elif command -v pacman &>/dev/null; then
        PKG_MANAGER="pacman"
    else
        error "Gestor de paquetes no soportado. Instala los paquetes manualmente."
        exit 1
    fi
    info "Gestor de paquetes detectado: $PKG_MANAGER"
}

install_pkg() {
    local pkg="$1"
    case "$PKG_MANAGER" in
        apt)    apt-get install -y "$pkg" ;;
        dnf)    dnf install -y "$pkg" ;;
        pacman) pacman -S --noconfirm "$pkg" ;;
    esac
}

get_real_user() {
    # Obtiene el usuario real (no root) que invoco sudo
    REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo '')}"
    if [[ -z "$REAL_USER" || "$REAL_USER" == "root" ]]; then
        error "No se pudo detectar el usuario real. Ejecuta con: sudo ./setup_acceso_remoto.sh"
        exit 1
    fi
    REAL_HOME=$(eval echo "~$REAL_USER")
    info "Usuario detectado: $REAL_USER (home: $REAL_HOME)"
}

show_help() {
    cat <<'HELP'
Uso: sudo ./setup_acceso_remoto.sh [OPCION]

Opciones:
  --tailscale    Instalar solo Tailscale (recomendado, sin abrir puertos)
  --ssh          Configurar solo SSH hardened (puerto expuesto + fail2ban)
  --ambos        Configurar ambos metodos
  --desinstalar  Revertir cambios y desinstalar
  -h, --help     Mostrar esta ayuda

Sin opciones se muestra un menu interactivo.
HELP
}

# ── SSH hardened ─────────────────────────────────────────────────────────────
SSH_PORT=2222

setup_ssh_keys() {
    info "Configurando autenticacion por clave SSH..."

    local ssh_dir="$REAL_HOME/.ssh"
    local key_file="$ssh_dir/id_ed25519"

    # Crear directorio .ssh si no existe
    if [[ ! -d "$ssh_dir" ]]; then
        mkdir -p "$ssh_dir"
        chmod 700 "$ssh_dir"
        chown "$REAL_USER:$REAL_USER" "$ssh_dir"
    fi

    # Generar clave si no existe
    if [[ ! -f "$key_file" ]]; then
        info "Generando par de claves Ed25519..."
        sudo -u "$REAL_USER" ssh-keygen -t ed25519 -f "$key_file" -N "" \
            -C "${REAL_USER}@$(hostname)-$(date +%Y%m%d)"
        ok "Clave generada: $key_file"
    else
        ok "Ya existe una clave Ed25519: $key_file"
    fi

    # Asegurar que authorized_keys existe
    local auth_keys="$ssh_dir/authorized_keys"
    if [[ ! -f "$auth_keys" ]]; then
        touch "$auth_keys"
    fi

    # Agregar la clave publica si no esta ya
    local pubkey
    pubkey=$(cat "${key_file}.pub")
    if ! grep -qF "$pubkey" "$auth_keys" 2>/dev/null; then
        echo "$pubkey" >> "$auth_keys"
        ok "Clave publica agregada a authorized_keys"
    fi

    chmod 600 "$auth_keys"
    chown "$REAL_USER:$REAL_USER" "$auth_keys"

    echo ""
    warn "=== IMPORTANTE: COPIA ESTA CLAVE PRIVADA A TU PORTATIL ==="
    echo ""
    info "Ejecuta esto DESDE tu portatil antes de irte de viaje:"
    echo ""
    printf "${BOLD}  scp -P %s %s@<IP_LOCAL>:%s ~/. ssh/minipc_key${NC}\n" \
        "$SSH_PORT" "$REAL_USER" "$key_file"
    echo ""
    info "O copia el contenido manualmente:"
    echo ""
    cat "$key_file"
    echo ""
    warn "================================================================"
    echo ""
}

harden_sshd() {
    info "Hardening de SSH en puerto $SSH_PORT..."

    # Instalar openssh-server si no esta
    if ! command -v sshd &>/dev/null; then
        install_pkg openssh-server
    fi

    local sshd_conf="/etc/ssh/sshd_config"
    local backup="/etc/ssh/sshd_config.backup.$(date +%Y%m%d%H%M%S)"

    # Backup
    cp "$sshd_conf" "$backup"
    ok "Backup de sshd_config: $backup"

    # Crear configuracion segura en directorio drop-in (si existe) o modificar directamente
    local drop_in="/etc/ssh/sshd_config.d/99-hardened.conf"

    cat > "$drop_in" <<SSHCONF
# Generado por setup_acceso_remoto.sh — $(date)
# Configuracion SSH hardened para acceso remoto seguro

# Puerto no estandar (evita la mayoria de bots)
Port $SSH_PORT

# Solo protocolo 2
Protocol 2

# Autenticacion solo por clave publica
PubkeyAuthentication yes
PasswordAuthentication no
PermitEmptyPasswords no
ChallengeResponseAuthentication no
UsePAM yes

# Deshabilitar login como root
PermitRootLogin no

# Limitar intentos de autenticacion
MaxAuthTries 3
MaxSessions 3
LoginGraceTime 30

# Deshabilitar metodos innecesarios
X11Forwarding no
PermitTunnel no
AllowAgentForwarding no
AllowTcpForwarding yes

# Solo permitir tu usuario
AllowUsers $REAL_USER

# Desconectar sesiones inactivas (10 min)
ClientAliveInterval 300
ClientAliveCountMax 2

# Logging
LogLevel VERBOSE
SSHCONF

    ok "Configuracion hardened escrita en: $drop_in"

    # Validar configuracion
    if sshd -t 2>/dev/null; then
        ok "Configuracion SSH validada correctamente"
    else
        error "Error en la configuracion SSH. Restaurando backup..."
        rm -f "$drop_in"
        exit 1
    fi

    # Reiniciar SSH
    systemctl restart sshd || systemctl restart ssh
    ok "Servicio SSH reiniciado en puerto $SSH_PORT"
}

setup_fail2ban() {
    info "Instalando y configurando fail2ban..."

    install_pkg fail2ban

    cat > /etc/fail2ban/jail.d/ssh-hardened.conf <<F2B
[sshd]
enabled  = true
port     = $SSH_PORT
filter   = sshd
logpath  = /var/log/auth.log
maxretry = 3
bantime  = 3600
findtime = 600
banaction = iptables-multiport
F2B

    systemctl enable fail2ban
    systemctl restart fail2ban
    ok "fail2ban configurado: 3 intentos -> ban 1 hora"
}

setup_ufw() {
    info "Configurando firewall (UFW)..."

    if ! command -v ufw &>/dev/null; then
        install_pkg ufw
    fi

    # Reglas basicas
    ufw default deny incoming
    ufw default allow outgoing

    # Permitir SSH en puerto custom con rate limiting
    ufw limit "$SSH_PORT/tcp" comment "SSH hardened - rate limited"

    # Activar firewall
    echo "y" | ufw enable
    ok "Firewall activado. Solo abierto el puerto $SSH_PORT/tcp con rate limiting"

    ufw status verbose
}

setup_ssh_completo() {
    info "=== Configuracion SSH Hardened ==="
    echo ""

    get_real_user
    harden_sshd
    setup_ssh_keys
    setup_fail2ban
    setup_ufw

    echo ""
    ok "=== SSH hardened configurado ==="
    echo ""
    info "Resumen de seguridad:"
    echo "  - Puerto SSH: $SSH_PORT (no el 22 por defecto)"
    echo "  - Autenticacion: solo clave publica (passwords deshabilitados)"
    echo "  - Fail2ban: 3 intentos fallidos = ban 1 hora"
    echo "  - Firewall: solo puerto $SSH_PORT abierto, con rate limiting"
    echo "  - Root login: deshabilitado"
    echo "  - Solo usuario permitido: $REAL_USER"
    echo ""
    warn "PASOS QUE QUEDAN (manual):"
    echo "  1. Copia la clave privada a tu portatil (ver arriba)"
    echo "  2. En el router: redirige el puerto $SSH_PORT externo -> $SSH_PORT interno"
    echo "     (busca 'Port Forwarding' en la config del router)"
    echo "  3. Averigua tu IP publica: curl ifconfig.me"
    echo "  4. Desde el portatil: ssh -i ~/. ssh/minipc_key -p $SSH_PORT $REAL_USER@<TU_IP_PUBLICA>"
    echo ""
    warn "CONSEJO: Usa un DNS dinamico (DuckDNS, No-IP) si tu IP publica cambia."
    echo ""
}

# ── Tailscale ────────────────────────────────────────────────────────────────
setup_tailscale() {
    info "=== Configuracion Tailscale (VPN mesh) ==="
    echo ""

    # Instalar Tailscale
    if ! command -v tailscale &>/dev/null; then
        info "Instalando Tailscale..."
        curl -fsSL https://tailscale.com/install.sh | sh
        ok "Tailscale instalado"
    else
        ok "Tailscale ya esta instalado"
    fi

    # Habilitar y arrancar
    systemctl enable tailscaled
    systemctl start tailscaled

    # Instalar SSH si no existe (para acceso por Tailscale SSH)
    if ! command -v sshd &>/dev/null; then
        install_pkg openssh-server
        systemctl enable sshd || systemctl enable ssh
        systemctl start sshd || systemctl start ssh
    fi

    echo ""
    info "Ejecuta ahora el siguiente comando para vincular este equipo a tu cuenta:"
    echo ""
    printf "  ${BOLD}sudo tailscale up --ssh${NC}\n"
    echo ""
    info "Esto abrira un enlace para autenticarte con tu cuenta de Tailscale."
    info "El flag --ssh habilita Tailscale SSH (sin configurar nada mas)."
    echo ""
    ok "=== Tailscale configurado ==="
    echo ""
    info "Despues de vincular, desde tu portatil (con Tailscale instalado):"
    echo ""
    echo "  # Ver el nombre/IP de tu mini PC en la red Tailscale"
    echo "  tailscale status"
    echo ""
    echo "  # Conectar desde el portatil"
    echo "  ssh usuario@<nombre-minipc>"
    echo "  # o usar la IP de Tailscale (100.x.x.x)"
    echo "  ssh usuario@100.x.x.x"
    echo ""
    info "Ventajas de Tailscale:"
    echo "  - NO necesitas abrir puertos en el router"
    echo "  - NO necesitas saber tu IP publica"
    echo "  - NO necesitas DNS dinamico"
    echo "  - Cifrado WireGuard punto a punto"
    echo "  - Funciona detras de NAT, firewalls y redes moviles"
    echo "  - Gratis para uso personal (hasta 100 dispositivos)"
    echo ""
}

# ── Desinstalar ──────────────────────────────────────────────────────────────
desinstalar() {
    info "=== Revirtiendo cambios ==="

    # Revertir SSH
    if [[ -f /etc/ssh/sshd_config.d/99-hardened.conf ]]; then
        rm -f /etc/ssh/sshd_config.d/99-hardened.conf
        systemctl restart sshd 2>/dev/null || systemctl restart ssh 2>/dev/null
        ok "Configuracion SSH hardened eliminada"
    fi

    # Revertir fail2ban
    if [[ -f /etc/fail2ban/jail.d/ssh-hardened.conf ]]; then
        rm -f /etc/fail2ban/jail.d/ssh-hardened.conf
        systemctl restart fail2ban 2>/dev/null
        ok "Configuracion fail2ban eliminada"
    fi

    # Revertir UFW
    if command -v ufw &>/dev/null; then
        ufw delete limit "$SSH_PORT/tcp" 2>/dev/null
        info "Regla de firewall para puerto $SSH_PORT eliminada"
        info "UFW sigue activo. Revisa 'ufw status' si quieres desactivarlo."
    fi

    echo ""
    ok "Cambios revertidos. SSH vuelve a su configuracion por defecto."
    warn "Tailscale no se desinstala automaticamente. Usa: sudo tailscale down && sudo apt remove tailscale"
}

# ── Menu interactivo ─────────────────────────────────────────────────────────
menu_interactivo() {
    echo ""
    printf "${BOLD}╔══════════════════════════════════════════════════════╗${NC}\n"
    printf "${BOLD}║   Configuracion de acceso remoto seguro para Linux  ║${NC}\n"
    printf "${BOLD}╚══════════════════════════════════════════════════════╝${NC}\n"
    echo ""
    echo "  1) Tailscale (RECOMENDADO)"
    echo "     VPN mesh gratuita. Sin abrir puertos. Cifrado WireGuard."
    echo "     Ideal si no quieres tocar el router."
    echo ""
    echo "  2) SSH hardened"
    echo "     Puerto SSH blindado + fail2ban + firewall."
    echo "     Necesitas abrir un puerto en el router."
    echo ""
    echo "  3) Ambos (Tailscale + SSH hardened)"
    echo "     Maxima flexibilidad: Tailscale como primario, SSH como backup."
    echo ""
    echo "  4) Desinstalar"
    echo "     Revertir los cambios de este script."
    echo ""
    echo "  5) Salir"
    echo ""

    read -rp "Elige una opcion [1-5]: " opcion

    case "$opcion" in
        1) setup_tailscale ;;
        2) setup_ssh_completo ;;
        3) setup_tailscale; setup_ssh_completo ;;
        4) desinstalar ;;
        5) info "Saliendo."; exit 0 ;;
        *) error "Opcion no valida"; exit 1 ;;
    esac
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    check_root
    detect_distro

    case "${1:-}" in
        --tailscale)   setup_tailscale ;;
        --ssh)         setup_ssh_completo ;;
        --ambos)       setup_tailscale; setup_ssh_completo ;;
        --desinstalar) desinstalar ;;
        -h|--help)     show_help; exit 0 ;;
        "")            menu_interactivo ;;
        *)             error "Opcion desconocida: $1"; show_help; exit 1 ;;
    esac

    echo ""
    ok "Listo. Recuerda probar la conexion ANTES de irte de viaje."
}

main "$@"
