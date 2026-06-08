/* ═══════════════════════════════════════════════
   MCD KIOSK — Standalone JS App
   Chạy tại /kiosk (không cần OWL/React)
   ═══════════════════════════════════════════════ */
(function () {
    'use strict';
    // Chi chay tren trang /kiosk
    if (!document.getElementById('kiosk-app')) return;

    // ─── STATE ───────────────────────────────────
    const state = {
        screen: 'welcome',     // welcome | service | phone | order | payment | qr | success
        serviceType: null,     // 'eat_in' | 'take_out'
        categories: [],
        products: [],
        modifiers: {},
        activeCateg: null,
        cart: [],              // [{product, qty, modifier_note, modifier_price_extra}]
        modifierTarget: null,  // product being customized
        modifierCartIndex: null,
        modifierSelections: {}, // bom_line_id -> 'remove'|'extra'|'only'|null
        pendingOrder: null,    // result from server after payment
        customer: null,        // {id, name, phone, is_new, order_count, total_spent}
        paying: false,
        lastPaymentMethod: null,
    };

    const IDLE_TIMEOUT_MS = 3 * 60 * 1000;
    const IDLE_TIMEOUT_SCREENS = new Set(['service', 'phone', 'order', 'cart-detail', 'payment', 'qr', 'success']);
    let idleTimer = null;
    let idleTrackingReady = false;

    // ─── HELPERS ─────────────────────────────────
    const fmt = (n) => new Intl.NumberFormat('vi-VN').format(Math.round(n)) + 'đ';

    async function rpc(route, params = {}) {
        try {
            const res = await fetch(route, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ jsonrpc: '2.0', method: 'call', params }),
            });
            const data = await res.json();
            if (data.error) {
                console.error('[Kiosk RPC Error]', route, data.error);
                return null;
            }
            return data.result;
        } catch (e) {
            console.error('[Kiosk Fetch Error]', route, e);
            return null;
        }
    }

    function cartTotal() {
        return state.cart.reduce((s, item) =>
            s + item.qty * (item.product.price + item.modifier_price_extra), 0);
    }

    function cartCount() {
        return state.cart.reduce((s, i) => s + i.qty, 0);
    }

    function formatPhoneDisplay(phone) {
        if (!phone) return '09xx xxx xxx';
        return phone.replace(/(\d{3})(\d{0,3})(\d{0,4}).*/, (_, a, b, c) =>
            [a, b, c].filter(Boolean).join(' ')
        );
    }

    function setPhoneDisplay(phone) {
        const disp = document.getElementById('phone-display');
        if (!disp) return;
        disp.textContent = formatPhoneDisplay(phone);
        disp.classList.toggle('is-placeholder', !phone);
    }

    // ─── SCREEN MANAGER ──────────────────────────
    function showScreen(name) {
        state.screen = name;
        document.querySelectorAll('.kiosk-screen').forEach(el => el.classList.remove('active'));
        const el = document.getElementById('screen-' + name);
        if (el) el.classList.add('active');
        resetIdleTimer();
    }

    function clearIdleTimer() {
        if (idleTimer) {
            clearTimeout(idleTimer);
            idleTimer = null;
        }
    }

    function resetIdleTimer() {
        if (!idleTrackingReady) return;
        clearIdleTimer();
        if (!IDLE_TIMEOUT_SCREENS.has(state.screen) || state.paying) return;

        idleTimer = setTimeout(() => {
            if (IDLE_TIMEOUT_SCREENS.has(state.screen) && !state.paying && window.KioskApp) {
                window.KioskApp.resetToWelcome();
            }
        }, IDLE_TIMEOUT_MS);
    }

    function bindIdleTracking() {
        if (idleTrackingReady) return;
        idleTrackingReady = true;
        ['click', 'touchstart', 'pointerdown', 'keydown', 'mousemove'].forEach(eventName => {
            document.addEventListener(eventName, resetIdleTimer, { passive: true });
        });
        resetIdleTimer();
    }

    // ─── BUILD APP ───────────────────────────────
    function buildApp() {
        const app = document.getElementById('kiosk-app');
        app.innerHTML = `
        <!-- WELCOME -->
        <div id="screen-welcome" class="kiosk-screen" onclick="KioskApp.goService()">
            <img class="welcome-logo" src="/mcd_backend_theme/static/src/img/logo-mcdonalds.png" alt="McDonald's">
            <div class="welcome-title">McDonald's</div>
            <div class="welcome-sub">Đặt món nhanh, không cần chờ đợi</div>
            <div class="welcome-tap">Chạm để bắt đầu</div>
        </div>

        <!-- SERVICE TYPE -->
        <div id="screen-service" class="kiosk-screen">
            <div class="service-title">Bạn muốn dùng bữa như thế nào?</div>
            <div class="service-options">
                <div class="service-card" id="card-eat-in" onclick="KioskApp.selectService('eat_in')">
                    <div class="icon">🪑</div>
                    <div class="label">Ăn tại chỗ</div>
                    <div class="desc">Ngồi thoải mái trong nhà hàng</div>
                </div>
                <div class="service-card" id="card-take-out" onclick="KioskApp.selectService('take_out')">
                    <div class="icon">🛍️</div>
                    <div class="label">Mang đi</div>
                    <div class="desc">Đóng gói để mang về</div>
                </div>
            </div>
            <button class="service-next-btn" id="service-next-btn" disabled onclick="KioskApp.goPhone()">
                Tiếp tục →
            </button>
        </div>

        <!-- PHONE -->
        <div id="screen-phone" class="kiosk-screen kiosk-phone-screen">
            <div class="phone-title">Nhập số điện thoại</div>
            <div class="phone-subtitle">Để nhận diện khách quen, có thể bỏ qua bước này</div>
            <div class="phone-card">
                <div id="phone-display" class="phone-display is-placeholder">09xx xxx xxx</div>
                <div class="phone-keypad">
                    <button class="numpad-btn" onclick="KioskApp.phoneInput('1')">1</button>
                    <button class="numpad-btn" onclick="KioskApp.phoneInput('2')">2</button>
                    <button class="numpad-btn" onclick="KioskApp.phoneInput('3')">3</button>
                    <button class="numpad-btn" onclick="KioskApp.phoneInput('4')">4</button>
                    <button class="numpad-btn" onclick="KioskApp.phoneInput('5')">5</button>
                    <button class="numpad-btn" onclick="KioskApp.phoneInput('6')">6</button>
                    <button class="numpad-btn" onclick="KioskApp.phoneInput('7')">7</button>
                    <button class="numpad-btn" onclick="KioskApp.phoneInput('8')">8</button>
                    <button class="numpad-btn" onclick="KioskApp.phoneInput('9')">9</button>
                    <button class="numpad-btn danger" onclick="KioskApp.phoneInput('clear')">Xóa</button>
                    <button class="numpad-btn" onclick="KioskApp.phoneInput('0')">0</button>
                    <button class="numpad-btn danger" onclick="KioskApp.phoneInput('del')">⌫</button>
                </div>
                <div id="customer-info" class="customer-info">
                    Nhập SĐT để tìm kiếm
                </div>
            </div>
            <div class="phone-actions">
                <button class="phone-skip-btn" onclick="KioskApp.skipPhone()">Bỏ qua</button>
                <button class="phone-confirm-btn" onclick="KioskApp.confirmPhone()" id="phone-confirm-btn">Xác nhận →</button>
            </div>
        </div>

        <!-- ORDER -->
        <div id="screen-order" class="kiosk-screen">
            <div class="kiosk-header">
                <img class="logo" src="/mcd_backend_theme/static/src/img/logo-mcdonalds.png" alt="McDonald's">
                <div class="kiosk-brand">
                    <span class="title">McDonald's Kiosk</span>
                    <span class="kiosk-subtitle">Chọn món và kiểm tra đơn trước khi thanh toán</span>
                </div>
                <span class="service-badge" id="service-label">-</span>
                <button class="back-btn" onclick="KioskApp.goService()">← Đổi</button>
            </div>

            <div class="kiosk-left">
                <div class="kiosk-menu-head">
                    <div>
                        <div class="menu-eyebrow">Menu hôm nay</div>
                        <div class="menu-title">Bạn muốn dùng món gì?</div>
                    </div>
                    <div class="menu-hint">Chạm vào món để thêm vào giỏ</div>
                </div>
                <div class="categ-bar" id="categ-bar"></div>
                <div class="product-grid" id="product-grid"></div>
            </div>

            <div class="kiosk-cart kiosk-cart-compact">
                <div class="cart-header">
                    Giỏ hàng
                    <span class="cart-badge" id="cart-badge">0</span>
                </div>
                <div class="cart-items" id="cart-items">
                    <div class="cart-empty">Chưa có món nào</div>
                </div>
                <div class="cart-footer">
                    <div class="cart-total">
                        <span>Tổng cộng</span>
                        <span id="cart-total-val">0đ</span>
                    </div>
                    <button class="checkout-btn" id="checkout-btn" disabled onclick="KioskApp.goCartDetail()">
                        Xem đơn hàng chi tiết
                    </button>
                </div>
            </div>
        </div>

        <!-- CART DETAIL -->
        <div id="screen-cart-detail" class="kiosk-screen">
            <div class="detail-header">
                <button class="detail-back" onclick="KioskApp.goOrder()">← Chọn thêm món</button>
                <div>
                    <div class="detail-title">Đơn hàng của bạn</div>
                    <div class="detail-subtitle">Kiểm tra món, số lượng và tùy chỉnh burger trước khi thanh toán</div>
                </div>
                <span class="service-badge" id="detail-service-label">-</span>
            </div>
            <div class="detail-body" id="detail-items"></div>
            <div class="detail-footer">
                <div class="detail-total">
                    <span>Tổng cộng</span>
                    <strong id="detail-total-val">0</strong>
                </div>
                <button class="detail-pay-btn" id="detail-pay-btn" disabled onclick="KioskApp.goPayment()">Thanh toán</button>
            </div>
        </div>

        <!-- MODIFIER POPUP -->
        <div class="modifier-overlay hidden" id="modifier-overlay">
            <div class="modifier-box">
                <div class="modifier-header">
                    <img id="mod-img" src="" alt=""/>
                    <div>
                        <h3 id="mod-name"></h3>
                        <div class="mh-price" id="mod-price"></div>
                    </div>
                </div>
                <div class="modifier-body" id="modifier-body"></div>
                <div class="modifier-footer">
                    <button class="mod-cancel" onclick="KioskApp.closeModifier()">Hủy</button>
                    <button class="mod-confirm" onclick="KioskApp.confirmModifier()">✓ Lưu tùy chỉnh</button>
                </div>
            </div>
        </div>

        <!-- PAYMENT -->
        <div id="screen-payment" class="kiosk-screen">
            <div class="payment-panel">
                <div class="payment-eyebrow">Thanh toán đơn hàng</div>
                <div class="payment-title">Chọn phương thức thanh toán</div>
                <div class="payment-amount" id="payment-amount">0đ</div>
                <div class="payment-methods">
                    <div class="pay-btn" onclick="KioskApp.payWith('cash')">
                        <div class="pay-icon">💵</div>
                        <div class="pay-label">Tiền mặt tại quầy</div>
                    </div>
                    <div class="pay-btn" onclick="KioskApp.payWith('card')">
                        <div class="pay-icon">💳</div>
                        <div class="pay-label">Thanh toán thẻ</div>
                    </div>
                    <div class="pay-btn" onclick="KioskApp.payWith('qr')">
                        <div class="pay-icon">📱</div>
                        <div class="pay-label">Mã QR</div>
                    </div>
                </div>
                <div class="payment-status" id="payment-status"></div>
                <button class="payment-back" onclick="KioskApp.goCartDetail()">← Quay lại giỏ hàng</button>
            </div>
        </div>

        <!-- QR SCREEN -->
        <div id="screen-qr" class="kiosk-screen">
            <div class="qr-title">Quét mã QR để thanh toán</div>
            <div class="qr-amount" id="qr-amount">0đ</div>
            <img class="qr-img" id="qr-img" src="" alt="QR Code"/>
            <div class="qr-hint">Mở app ngân hàng → Quét mã → Xác nhận thanh toán</div>
            <button class="qr-done-btn" onclick="KioskApp.confirmQR()">✓ Đã thanh toán</button>
            <button class="qr-back-btn" onclick="KioskApp.goPayment()">← Đổi phương thức</button>
        </div>

        <!-- SUCCESS -->
        <div id="screen-success" class="kiosk-screen">
            <div class="success-panel">
                <div class="success-icon">✓</div>
                <div class="success-title">Đặt hàng thành công</div>
                <div class="success-payment" id="success-payment-label">Thanh toán thành công</div>
                <div class="success-label">Số đơn của bạn</div>
                <div class="success-num" id="success-order-num">#0000</div>
                <div class="success-hint">Vui lòng chờ nhân viên gọi số. Đơn hàng đang được chuẩn bị.</div>
                <button class="success-back" onclick="KioskApp.resetToWelcome()">Đặt thêm</button>
            </div>
        </div>
        `;
    }

    // ─── LOAD MENU ───────────────────────────────
    async function loadMenu() {
        const grid = document.getElementById('product-grid');
        if (grid) grid.innerHTML = '<div style="padding:40px;color:#888;text-align:center;">⏳ Đang tải menu...</div>';

        const data = await rpc('/mcd_kiosk/menu');
        if (!data) {
            if (grid) grid.innerHTML = '<div style="padding:40px;color:#c00;text-align:center;">❌ Không tải được menu. Vui lòng thử lại.</div>';
            console.error('[Kiosk] /mcd_kiosk/menu returned null - check server logs');
            return;
        }
        state.categories = data.categories || [];
        state.products   = data.products   || [];
        state.modifiers  = data.modifiers  || {};

        renderCategories();
        renderProducts();
    }

    function renderCategories() {
        const bar = document.getElementById('categ-bar');
        bar.innerHTML = `<button class="categ-btn ${!state.activeCateg ? 'active' : ''}"
            onclick="KioskApp.filterCateg(null)">Tất cả</button>`;
        state.categories.forEach(c => {
            bar.innerHTML += `<button class="categ-btn ${state.activeCateg === c.id ? 'active' : ''}"
                onclick="KioskApp.filterCateg(${c.id})">${c.name}</button>`;
        });
    }

    function renderProducts() {
        const grid   = document.getElementById('product-grid');
        const filtered = state.activeCateg
            ? state.products.filter(p => p.categ_ids.includes(state.activeCateg))
            : state.products;

        if (!filtered.length) {
            grid.innerHTML = '<div style="padding:40px;color:#aaa;text-align:center;">Không có sản phẩm</div>';
            return;
        }
        grid.innerHTML = filtered.map(p => {
            const unavailable = p.unavailable || p.mcd_pos_unavailable;
            const reason = p.unavailable_reason || p.mcd_pos_unavailable_reason || 'Nguyên liệu của món này hiện không đủ.';
            return `
            <div class="product-card ${unavailable ? 'product-card-unavailable' : ''}" onclick="${unavailable ? `KioskApp.showUnavailable(${p.id})` : `KioskApp.addProduct(${p.id})`}">
                <img src="${p.image}" alt="${p.name}" loading="lazy" onerror="this.src='/web/static/img/placeholder.png'"/>
                ${unavailable ? '<div class="product-unavailable-badge">Tạm dừng</div>' : ''}
                <div class="pname">${p.name}</div>
                <div class="product-card-foot">
                    <div class="pprice">${fmt(p.price)}</div>
                    <div class="product-add-mark">+</div>
                </div>
                ${unavailable ? `<div class="product-unavailable-reason">${reason}</div>` : ''}
            </div>
        `;
        }).join('');
    }

    function renderCart() {
        const items   = document.getElementById('cart-items');
        const badge   = document.getElementById('cart-badge');
        const total   = document.getElementById('cart-total-val');
        const btn     = document.getElementById('checkout-btn');
        const count   = cartCount();
        const tot     = cartTotal();

        if (badge) badge.textContent = count;
        if (total) total.textContent = fmt(tot);
        if (btn) {
            btn.disabled = count === 0;
            btn.textContent = 'Xem đơn hàng chi tiết';
        }

        if (!items) {
            renderCartDetail();
            return;
        }
        if (!state.cart.length) {
            items.innerHTML = '<div class="cart-empty">Chưa có món nào</div>';
            renderCartDetail();
            return;
        }
        items.innerHTML = state.cart.map((item, idx) => `
            <div class="cart-item">
                <img class="cart-item-img" src="${item.product.image}" alt=""
                    onerror="this.src='/web/static/img/placeholder.png'"/>
                <div class="cart-item-info">
                    <div class="cart-item-name">${item.product.name}</div>
                    ${item.modifier_note ? `<div class="cart-item-mod">${item.modifier_note}</div>` : ''}
                    <div class="cart-item-price">${fmt(item.qty * (item.product.price + item.modifier_price_extra))}</div>
                </div>
                <div class="cart-item-qty">
                    <button class="qty-btn" onclick="KioskApp.changeQty(${idx}, -1)">-</button>
                    <span class="qty-num">${item.qty}</span>
                    <button class="qty-btn" onclick="KioskApp.changeQty(${idx}, 1)">+</button>
                </div>
            </div>
        `).join('');
        renderCartDetail();
    }

    function hasEditableModifiers(product) {
        if (!isBurgerProduct(product)) return false;
        return (state.modifiers[product.tmpl_id] || []).some(ing => (ing.allowed_actions || []).length > 0);
    }

    function isBurgerProduct(product) {
        const categoryNames = (product.categ_ids || [])
            .map(id => (state.categories.find(c => c.id === id)?.name || '').toLowerCase());
        return categoryNames.some(name => name.includes('burger'));
    }

    function parseModifierJson(value) {
        if (!value) return {};
        try {
            const data = JSON.parse(value);
            return data && typeof data === 'object' ? data : {};
        } catch (e) {
            return {};
        }
    }

    function buildModifierPayload(product, selections) {
        const ings = state.modifiers[product.tmpl_id] || [];
        const parts = [];
        let priceExtra = 0;
        for (const [bom_line_id, action] of Object.entries(selections || {})) {
            const ing = ings.find(i => i.bom_line_id == bom_line_id);
            if (!ing) continue;
            const labels = { remove: 'Bỏ', extra: 'Thêm', only: 'Chỉ' };
            parts.push(`${labels[action]} ${ing.name}`);
            if (action === 'extra') {
                priceExtra += ing.price_unit || 0;
            }
        }
        return {
            note: parts.join(', '),
            json: JSON.stringify(selections || {}),
            priceExtra,
        };
    }

    function renderCartDetail() {
        const items = document.getElementById('detail-items');
        const total = document.getElementById('detail-total-val');
        const payBtn = document.getElementById('detail-pay-btn');
        const serviceLabel = document.getElementById('detail-service-label');
        if (!items || !total || !payBtn) return;

        if (serviceLabel) {
            serviceLabel.textContent = state.serviceType === 'take_out' ? 'TAKE OUT' : 'EAT IN';
        }
        total.textContent = fmt(cartTotal());
        payBtn.disabled = !state.cart.length;

        if (!state.cart.length) {
            items.innerHTML = '<div class="detail-empty">Chưa có món nào trong đơn hàng</div>';
            return;
        }

        items.innerHTML = state.cart.map((item, idx) => {
            const editable = hasEditableModifiers(item.product);
            return `
            <div class="detail-item">
                <img class="detail-item-img" src="${item.product.image}" alt=""
                    onerror="this.src='/web/static/img/placeholder.png'"/>
                <div class="detail-item-info">
                    <div class="detail-item-name">${item.product.name}</div>
                    ${item.modifier_note ? `<div class="detail-item-mod">${item.modifier_note}</div>` : '<div class="detail-item-mod muted">Giữ nguyên công thức</div>'}
                    <div class="detail-item-price">${fmt(item.product.price + item.modifier_price_extra)} / món</div>
                </div>
                <div class="detail-item-actions">
                    <div class="cart-item-qty">
                        <button class="qty-btn" onclick="KioskApp.changeQty(${idx}, -1)">-</button>
                        <span class="qty-num">${item.qty}</span>
                        <button class="qty-btn" onclick="KioskApp.changeQty(${idx}, 1)">+</button>
                    </div>
                    <button class="customize-btn" ${editable ? '' : 'disabled'} onclick="KioskApp.customizeCartItem(${idx})">
                        Tùy chỉnh món
                    </button>
                </div>
            </div>`;
        }).join('');
    }

    // MODIFIER
    function openModifier(product, cartIndex = null) {
        state.modifierTarget = product;
        state.modifierCartIndex = cartIndex;
        state.modifierSelections = cartIndex === null ? {} : parseModifierJson(state.cart[cartIndex]?.modifier_json);
        const ings = state.modifiers[product.tmpl_id] || [];

        document.getElementById('mod-img').src = product.image;
        document.getElementById('mod-name').textContent = product.name;
        document.getElementById('mod-price').textContent = fmt(product.price);

        const body = document.getElementById('modifier-body');
        const editableIngredients = ings.filter(ing => (ing.allowed_actions || []).length > 0);
        if (!editableIngredients.length) {
            body.innerHTML = '<div style="padding:20px;color:#888;text-align:center;">Món này không có nguyên liệu có thể tùy chỉnh</div>';
        } else {
            body.innerHTML = editableIngredients.map(ing => {
                const actions = ing.allowed_actions || [];
                const btns = ['remove', 'extra', 'only']
                    .filter(a => actions.includes(a))
                    .map(a => {
                        const labels = { remove: 'Bỏ', extra: 'Thêm', only: 'Chỉ' };
                        return `<button class="mod-btn" id="mb-${ing.bom_line_id}-${a}"
                            onclick="KioskApp.toggleMod(${ing.bom_line_id}, '${a}')">${labels[a]}</button>`;
                    }).join('');
                const priceStr = ing.price_unit ? `<span class="modifier-ing-price">(+${fmt(ing.price_unit)})</span>` : '';
                return `<div class="modifier-ing-row">
                    <span class="modifier-ing-name">${ing.name}${priceStr}</span>
                    <div class="modifier-actions">${btns}</div>
                </div>`;
            }).join('');
        }
        updateModNote();
        Object.entries(state.modifierSelections).forEach(([bomLineId, action]) => {
            const btn = document.getElementById(`mb-${bomLineId}-${action}`);
            if (btn) btn.classList.add(`active-${action}`);
        });
        document.getElementById('modifier-overlay').classList.remove('hidden');
    }
    function updateModNote() {
        const ings = state.modifiers[state.modifierTarget?.tmpl_id] || [];
        const parts = [];
        for (const [bom_line_id, action] of Object.entries(state.modifierSelections)) {
            const ing = ings.find(i => i.bom_line_id == bom_line_id);
            if (!ing) continue;
            const labels = { remove: 'Bỏ', extra: 'Thêm', only: 'Chỉ' };
            parts.push(`${labels[action]} ${ing.name}`);
        }
        const noteEl = document.getElementById('mod-note');
        if (noteEl) noteEl.textContent = parts.length ? parts.join(', ') : 'Giữ nguyên';
    }

    // ─── PUBLIC API ──────────────────────────────
    function setPaymentStatus(message = '', busy = false) {
        state.paying = busy;
        const status = document.getElementById('payment-status');
        if (status) {
            status.textContent = message;
            status.classList.toggle('active', !!message);
        }
        document.querySelectorAll('.pay-btn, .qr-done-btn').forEach(btn => {
            btn.classList.toggle('is-disabled', busy);
        });
    }

    window.KioskApp = {
        _phoneBuffer: '',

        goService() {
            showScreen('service');
        },

        // ── Phone screen ──
        phoneInput(val) {
            if (val === 'clear') {
                this._phoneBuffer = '';
            } else if (val === 'del') {
                this._phoneBuffer = this._phoneBuffer.slice(0, -1);
            } else if (this._phoneBuffer.length < 11) {
                this._phoneBuffer += val;
            }
            setPhoneDisplay(this._phoneBuffer);

            // Auto-lookup khi du 10 so
            if (this._phoneBuffer.length === 10) {
                this._lookupPhone(this._phoneBuffer);
            } else {
                const info = document.getElementById('customer-info');
                if (info) info.textContent = 'Nhập SĐT để tìm kiếm';
                state.customer = null;
            }
        },

        async _lookupPhone(phone) {
            const info = document.getElementById('customer-info');
            if (info) info.textContent = '🔍 Đang tìm...';
            const res = await rpc('/mcd_customer/lookup', { phone });
            if (res && res.success && res.customer) {
                state.customer = res.customer;
                const c = res.customer;
                const tag = c.is_new
                    ? '<span style="color:#22c55e">🆕 Khách mới</span>'
                    : `<span style="color:var(--yellow)">🔄 Khách quen — ${c.order_count} lần mua</span>`;
                const spent = c.total_spent
                    ? `<br/>Đã chi: <b>${new Intl.NumberFormat('vi-VN').format(c.total_spent)}đ</b>`
                    : '';
                if (info) info.innerHTML = `<b>${c.name}</b><br/>${tag}${spent}`;
            } else {
                state.customer = null;
                if (info) info.innerHTML = '<span style="color:#aaa">Số mới — sẽ tạo tài khoản</span>';
            }
        },

        async confirmPhone() {
            if (this._phoneBuffer.length >= 9) {
                await this._lookupPhone(this._phoneBuffer);
            }
            await this.goOrder();
        },

        skipPhone() {
            state.customer = null;
            this._phoneBuffer = '';
            this.goOrder();
        },

        selectService(type) {
            state.serviceType = type;
            document.getElementById('card-eat-in').classList.toggle('selected', type === 'eat_in');
            document.getElementById('card-take-out').classList.toggle('selected', type === 'take_out');
            document.getElementById('service-next-btn').disabled = false;
        },

        goPhone() {
            if (!state.serviceType) return;
            this._phoneBuffer = '';
            state.customer = null;
            setPhoneDisplay('');
            const info = document.getElementById('customer-info');
            if (info) info.textContent = 'Nhập SĐT để tìm kiếm';
            showScreen('phone');
        },

        async goOrder() {
            if (!state.serviceType) state.serviceType = 'eat_in';
            const lbl = document.getElementById('service-label');
            lbl.textContent = state.serviceType === 'eat_in' ? '🪑 Ăn tại chỗ' : '🛍️ Mang đi';
            showScreen('order');
            if (!state.products.length) {
                await loadMenu();
            } else {
                renderCategories();
                renderProducts();
            }
        },

        filterCateg(id) {
            state.activeCateg = id;
            renderCategories();
            renderProducts();
        },

        showUnavailable(productId) {
            const product = state.products.find(p => p.id === productId);
            const reason = product?.unavailable_reason || product?.mcd_pos_unavailable_reason || 'Nguyên liệu của món này hiện không đủ.';
            alert(reason);
        },

        addProduct(productId) {
            const product = state.products.find(p => p.id === productId);
            if (!product) return;
            if (product.unavailable || product.mcd_pos_unavailable) {
                this.showUnavailable(productId);
                return;
            }
            const existing = state.cart.find(i => i.product.id === productId && !i.modifier_note);
            if (existing) {
                existing.qty++;
            } else {
                state.cart.push({ product, qty: 1, modifier_note: '', modifier_json: '', modifier_price_extra: 0 });
            }
            renderCart();
        },

        goCartDetail() {
            if (!state.cart.length) return;
            renderCartDetail();
            showScreen('cart-detail');
        },

        customizeCartItem(idx) {
            const item = state.cart[idx];
            if (!item || !hasEditableModifiers(item.product)) return;
            openModifier(item.product, idx);
        },
        toggleMod(bom_line_id, action) {
            const cur = state.modifierSelections[bom_line_id];
            if (action === 'only') {
                state.modifierSelections = cur === action ? {} : { [bom_line_id]: 'only' };
            } else if (Object.values(state.modifierSelections).includes('only')) {
                return;
            } else if (cur === action) {
                delete state.modifierSelections[bom_line_id];
            } else {
                state.modifierSelections[bom_line_id] = action;
            }
            const ings = state.modifiers[state.modifierTarget?.tmpl_id] || [];
            ings.forEach(ing => {
                ['remove', 'extra', 'only'].forEach(a => {
                    const btn = document.getElementById(`mb-${ing.bom_line_id}-${a}`);
                    if (btn) {
                        btn.classList.remove('active-remove', 'active-extra', 'active-only');
                        if (state.modifierSelections[ing.bom_line_id] === a) {
                            btn.classList.add(`active-${a}`);
                        }
                    }
                });
            });
            updateModNote();
        },
        confirmModifier() {
            const product = state.modifierTarget;
            if (!product) return;
            const payload = buildModifierPayload(product, state.modifierSelections);
            if (state.modifierCartIndex !== null && state.cart[state.modifierCartIndex]) {
                const item = state.cart[state.modifierCartIndex];
                item.modifier_note = payload.note;
                item.modifier_json = payload.json;
                item.modifier_price_extra = payload.priceExtra;
            }
            this.closeModifier();
            renderCart();
        },
        closeModifier() {
            document.getElementById('modifier-overlay').classList.add('hidden');
            state.modifierTarget = null;
            state.modifierCartIndex = null;
            state.modifierSelections = {};
        },

        changeQty(idx, delta) {
            state.cart[idx].qty += delta;
            if (state.cart[idx].qty <= 0) state.cart.splice(idx, 1);
            renderCart();
        },

        goPayment() {
            document.getElementById('payment-amount').textContent = fmt(cartTotal());
            setPaymentStatus('', false);
            showScreen('payment');
        },

        async payWith(method) {
            if (state.paying || !state.cart.length) return;
            if (method === 'qr') {
                setPaymentStatus('Đang tạo mã QR thanh toán...', true);
                const total = cartTotal();
                const ref   = 'KIOSK-' + Date.now();
                const qr    = await rpc('/mcd_kiosk/payment/qr', { amount: total, order_ref: ref });
                setPaymentStatus('', false);
                if (!qr || !qr.qr_url) {
                    alert('Không tạo được mã QR, vui lòng thử lại.');
                    return;
                }
                document.getElementById('qr-amount').textContent = fmt(total);
                document.getElementById('qr-img').src = qr.qr_url;
                showScreen('qr');
                return;
            }
            await this.submitOrder(method);
        },

        async confirmQR() {
            if (state.paying) return;
            await this.submitOrder('qr');
        },

        async submitOrder(method) {
            if (state.paying || !state.cart.length) return;
            const paymentLabel = {
                cash: 'Đang ghi nhận thanh toán tiền mặt tại quầy...',
                card: 'Đang xác nhận thanh toán thẻ...',
                qr: 'Đang xác nhận thanh toán QR...',
            }[method] || 'Đang xác nhận thanh toán...';
            setPaymentStatus(paymentLabel, true);
            const lines = state.cart.map(item => ({
                product_id:           item.product.id,
                qty:                  item.qty,
                price_unit:           item.product.price,
                modifier_note:        item.modifier_note,
                modifier_json:        item.modifier_json || '',
                modifier_price_extra: item.modifier_price_extra,
            }));
            const result = await rpc('/mcd_kiosk/order', {
                service_type:   state.serviceType,
                lines,
                payment_method: method,
                partner_id:     state.customer ? state.customer.id : null,
                phone:          state.customer ? null : (this._phoneBuffer || null),
            });
            if (result && result.success) {
                state.pendingOrder = result;
                state.lastPaymentMethod = method;
                document.getElementById('success-order-num').textContent = result.order_name;
                const successLabel = {
                    cash: 'Thanh toán tiền mặt tại quầy thành công',
                    card: 'Thanh toán thẻ thành công',
                    qr: 'Thanh toán QR thành công',
                }[method] || 'Thanh toán thành công';
                const successPayment = document.getElementById('success-payment-label');
                if (successPayment) successPayment.textContent = successLabel;
                setPaymentStatus('', false);
                showScreen('success');
            } else {
                setPaymentStatus('', false);
                alert((result && result.error) || 'Đặt hàng thất bại, vui lòng thử lại!');
            }
        },

        resetToWelcome() {
            clearIdleTimer();
            state.cart         = [];
            state.serviceType  = null;
            state.pendingOrder = null;
            state.customer     = null;
            state.paying       = false;
            state.lastPaymentMethod = null;
            state.modifierTarget = null;
            state.modifierCartIndex = null;
            state.modifierSelections = {};
            this._phoneBuffer  = '';
            const modifierOverlay = document.getElementById('modifier-overlay');
            if (modifierOverlay) modifierOverlay.classList.add('hidden');
            const eatInCard = document.getElementById('card-eat-in');
            const takeOutCard = document.getElementById('card-take-out');
            const serviceNextBtn = document.getElementById('service-next-btn');
            if (eatInCard) eatInCard.classList.remove('selected');
            if (takeOutCard) takeOutCard.classList.remove('selected');
            if (serviceNextBtn) serviceNextBtn.disabled = true;
            setPaymentStatus('', false);
            renderCart();
            showScreen('welcome');
        },
    };

    // ─── INIT ─────────────────────────────────────
    function init() {
        buildApp();
        showScreen('welcome');
        bindIdleTracking();
        // Pre-load menu in background
        loadMenu();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
