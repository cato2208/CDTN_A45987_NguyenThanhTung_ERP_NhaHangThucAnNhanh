/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// ─── Helper ──────────────────────────────────────────────────────────────────

/**
 * Compute elapsed seconds between an ISO-8601 UTC string and now.
 * Clamped to [0, 999].
 */
function elapsed(isoUtc) {
    if (!isoUtc) return 0;
    const diff = Math.floor((Date.now() - new Date(isoUtc).getTime()) / 1000);
    return Math.max(0, Math.min(diff, 999));
}

/**
 * CSS class for the timer badge based on age.
 *   0-59s  → green
 *  60-119s → orange / warning
 *  120s+   → red / urgent
 */
function timerClass(sec) {
    if (sec >= 120) return "kds-timer--red";
    if (sec >= 60)  return "kds-timer--orange";
    return "kds-timer--green";
}

// ─── Component ───────────────────────────────────────────────────────────────

class KitchenDisplay extends Component {
    static template = "mcd_kds.KitchenDisplay";
    static props = ["*"];

    setup() {
        this.rpc = useService("rpc");
        this.notification = useService("notification");

        this.state = useState({
            orders: [],        // waiting orders, oldest first
            tick: 0,           // incremented every second to refresh timers
            serving: false,    // prevents double-click on SERVE
        });

        this._refreshTimer = null;
        this._tickTimer = null;

        onMounted(() => {
            this._loadOrders();
            // Refresh order list from server every 3 s
            this._refreshTimer = setInterval(() => this._loadOrders(), 3000);
            // Increment tick every second so timers re-render
            this._tickTimer = setInterval(() => { this.state.tick++; }, 1000);
        });

        onWillUnmount(() => {
            clearInterval(this._refreshTimer);
            clearInterval(this._tickTimer);
        });
    }

    // ── Private ──────────────────────────────────────────────────────────────

    async _loadOrders() {
        try {
            const orders = await this.rpc("/mcd_kds/get_orders", {});
            this.state.orders = orders || [];
        } catch (e) {
            console.error("[KDS] Failed to load orders:", e);
        }
    }

    // ── Template helpers (called from XML) ───────────────────────────────────

    elapsed(isoUtc) {
        // Access this.state.tick so OWL re-evaluates every tick
        void this.state.tick;
        return elapsed(isoUtc);
    }

    timerClass(isoUtc) {
        return timerClass(this.elapsed(isoUtc));
    }

    serviceLabel(type) {
        return type === "take_out" ? "TAKE OUT" : "EAT IN";
    }

    serviceClass(type) {
        return type === "take_out" ? "kds-badge--takeout" : "kds-badge--eatin";
    }

    // ── User actions ─────────────────────────────────────────────────────────

    async onServe(orderId) {
        if (this.state.serving) return;
        if (!orderId) return;
        if (!this.state.orders.length) {
            this.notification.add("Không có đơn nào đang chờ.", { type: "warning" });
            return;
        }
        this.state.serving = true;
        try {
            const res = await this.rpc("/mcd_kds/serve", { order_id: orderId });
            if (res && res.served_id) {
                // Optimistic remove from local state immediately
                this.state.orders = this.state.orders.filter(
                    (o) => o.id !== res.served_id
                );
            }
        } catch (e) {
            console.error("[KDS] Serve failed:", e);
        } finally {
            this.state.serving = false;
        }
    }
}

// Register the client action
registry.category("actions").add("mcd_kds.KitchenDisplay", KitchenDisplay);
