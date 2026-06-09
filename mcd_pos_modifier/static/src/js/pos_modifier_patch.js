/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { ProductsWidget } from "@point_of_sale/app/screens/product_screen/product_list/product_list";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { Order, Orderline, Product } from "@point_of_sale/app/store/models";
import { AbstractAwaitablePopup } from "@point_of_sale/app/popup/abstract_awaitable_popup";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";

const MCD_SIZE_ORDER = { S: 1, M: 2, L: 3 };
const MCD_SIZE_GROUP_BY_PRODUCT = new WeakMap();
const MCD_HIDDEN_CONTROL_BUTTONS = new Set([
    "RefundButton",
    "OrderlineCustomerNoteButton",
    "PromoCodeButton",
    "RewardButton",
]);

function mcdParseSizeProduct(product) {
    const name = (product?.display_name || product?.name || "").trim();
    const match = name.match(/^(.*)\s+\((S|M|L)\)$/i);
    if (!match) {
        return null;
    }
    return {
        baseName: match[1].trim(),
        size: match[2].toUpperCase(),
    };
}

function mcdSortBySize(a, b) {
    const sizeA = mcdParseSizeProduct(a)?.size || "";
    const sizeB = mcdParseSizeProduct(b)?.size || "";
    return (MCD_SIZE_ORDER[sizeA] || 99) - (MCD_SIZE_ORDER[sizeB] || 99);
}

function mcdProductTemplateId(product) {
    const tmplId = product?.product_tmpl_id;
    return Array.isArray(tmplId) ? tmplId[0] : tmplId;
}

function mcdProductLooksCustomizable(product) {
    const name = (product?.display_name || product?.name || "").toLowerCase();
    return Boolean(product?.mcd_can_customize) || /burger|bicmac|bigmac|hamburger|cheese/.test(name);
}

function mcdLineCanCustomize(pos, line) {
    if (!line || !line.product || line.qty <= 0 || !mcdProductLooksCustomizable(line.product)) {
        return false;
    }
    const key = mcdProductTemplateId(line.product);
    const ingredients = (pos.mcd_modifiers_by_tmpl || {})[key] || [];
    return ingredients.some((ingredient) => (ingredient.allowed_actions || []).length > 0);
}

export class SizeChoicePopup extends AbstractAwaitablePopup {
    static template = "mcd_pos_modifier.SizeChoicePopup";
    static defaultProps = {
        title: _t("Chọn kích cỡ"),
        groupName: "",
        products: [],
    };

    setup() {
        super.setup();
        const defaultProduct = (this.props.products || []).find((product) => {
            return mcdParseSizeProduct(product)?.size === "M";
        }) || (this.props.products || [])[0];
        this.state = useState({
            selectedProductId: defaultProduct?.id || false,
        });
    }

    get sortedProducts() {
        return [...(this.props.products || [])].sort(mcdSortBySize);
    }

    sizeLabel(product) {
        return mcdParseSizeProduct(product)?.size || product.display_name;
    }

    selectProduct(product) {
        this.state.selectedProductId = product.id;
    }

    isSelected(product) {
        return this.state.selectedProductId === product.id;
    }

    getPayload() {
        return {
            product_id: this.state.selectedProductId,
        };
    }
}

export class ModifierPopup extends AbstractAwaitablePopup {
    static template = "mcd_pos_modifier.ModifierPopup";
    static defaultProps = {
        confirmText: _t("Xác nhận"),
        cancelText: _t("Hủy"),
        title: _t("Tùy chỉnh món"),
        ingredients: [],
        basePrice: 0,
        productName: "",
    };

    setup() {
        super.setup();
        this.state = useState({ selections: {}, note: "", priceExtra: 0 });
    }

    get visibleIngredients() {
        return (this.props.ingredients || []).filter(
            i => (i.allowed_actions || []).length > 0 && i.category !== "Fixed" && i.category !== "Buns"
        );
    }

    toggleAction(ingredient, action) {
        const key = String(ingredient.bom_line_id);

        // Bấm lại cùng action → bỏ chọn
        if (this.state.selections[key] === action) {
            delete this.state.selections[key];
            this._recompute();
            return;
        }

        // Chọn "only" → xóa hết, chỉ giữ only này
        if (action === "only") {
            this.state.selections = { [key]: "only" };
            this._recompute();
            return;
        }

        // Đang có "only" → không cho chọn gì khác
        const hasOnly = Object.values(this.state.selections).includes("only");
        if (hasOnly) return;

        this.state.selections[key] = action;
        this._recompute();
    }

    isSelected(ingredient, action) {
        return this.state.selections[String(ingredient.bom_line_id)] === action;
    }

    isDisabled(ingredient, action) {
        const hasOnly = Object.values(this.state.selections).includes("only");
        if (!hasOnly) return false;
        return this.state.selections[String(ingredient.bom_line_id)] !== "only";
    }

    hasPrice(ingredient) {
        return Number(ingredient?.price_unit || 0) > 0;
    }

    formatPrice(value) {
        return Number(value || 0).toLocaleString("vi-VN");
    }

    _recompute() {
        const selections = this.state.selections;
        const onlyKey = Object.keys(selections).find(k => selections[k] === "only");
        const noteParts = [];
        let extra = 0;

        if (onlyKey) {
            // Only mode: ghi chú thôi, KHÔNG cộng tiền
            const onlyIng = this.props.ingredients.find(
                i => String(i.bom_line_id) === onlyKey
            );
            if (onlyIng) noteParts.push(`Chỉ ${onlyIng.name}`);
            for (const ing of this.visibleIngredients) {
                if (String(ing.bom_line_id) !== onlyKey) {
                    noteParts.push(`Bỏ ${ing.name}`);
                }
            }
            extra = 0; // Không tính phí
        } else {
            // Normal mode
            for (const ing of this.visibleIngredients) {
                const action = selections[String(ing.bom_line_id)];
                if (!action) continue;
                const labelMap = { remove: "Bỏ", extra: "Thêm" };
                noteParts.push(`${labelMap[action] || action} ${ing.name}`);
                if (action === "extra") {
                    extra += Number(ing.price_unit || 0);
                }
                // "remove" = miễn phí
            }
        }

        this.state.note = noteParts.join(", ");
        this.state.priceExtra = extra;
    }

    getPayload() {
        const selections = this.state.selections;
        const isOnly = Object.values(selections).includes("only");
        return {
            modifier_json: JSON.stringify(selections || {}),
            modifier_note: this.state.note || "",
            modifier_price_extra: this.state.priceExtra || 0,
            modifier_is_only: isOnly,  // flag để orderline biết không cộng tiền
        };
    }
}

export class CustomizeButton extends Component {
    static template = "mcd_pos_modifier.CustomizeButton";
    static props = {};

    setup() {
        this.pos = usePos();
        this.popup = useService("popup");
    }

    get currentOrder() {
        return this.pos.get_order();
    }

    get canCustomize() {
        return Boolean(this._getCustomizableLine());
    }

    _getCustomizableLine() {
        const order = this.currentOrder;
        if (!order) {
            return null;
        }
        const selectedLine = order.get_selected_orderline();
        if (mcdLineCanCustomize(this.pos, selectedLine)) {
            return selectedLine;
        }
        const lines = [...order.get_orderlines()].reverse();
        return lines.find((line) => mcdLineCanCustomize(this.pos, line)) || null;
    }

    async onClick() {
        const line = this._getCustomizableLine();
        if (!line) {
            await this.popup.add(ErrorPopup, {
                title: _t("Không có món để tùy chỉnh"),
                body: _t("Chỉ các món burger có nguyên liệu nhân bánh mới hỗ trợ tùy chỉnh."),
            });
            return;
        }
        if (this.pos._mcd_open_modifier_popup_for_line) {
            await this.pos._mcd_open_modifier_popup_for_line(line);
        }
    }

    async click() {
        await this.onClick();
    }
}

patch(PosStore.prototype, {
    async _processData(loadedData) {
        await super._processData(loadedData);
        this.mcd_modifiers_by_tmpl = loadedData["mcd_modifiers_by_tmpl"] || {};
    },

    async addProductToCurrentOrder(product, options = {}) {
        if (Number.isInteger(product)) {
            product = this.db.get_product_by_id(product);
        }
        if (product?.mcd_pos_unavailable) {
            await this.popup.add(ErrorPopup, {
                title: _t("Món đang tạm dừng phục vụ"),
                body: product.mcd_pos_unavailable_reason || _t("Nguyên liệu của món này hiện không đủ."),
            });
            return;
        }
        return await super.addProductToCurrentOrder(product, options);
    },
});

patch(Product.prototype, {
    isConfigurable() {
        if (mcdParseSizeProduct(this)) {
            return false;
        }
        return super.isConfigurable(...arguments);
    },
});

patch(ProductScreen.prototype, {
    get controlButtons() {
        return super.controlButtons.filter((button) => {
            return !MCD_HIDDEN_CONTROL_BUTTONS.has(button.name || button.component?.name);
        });
    },

    setup() {
        super.setup(...arguments);
        this.pos = usePos();
        this.popup = useService("popup");
        this.pos._mcd_open_modifier_popup_for_line = this._mcdOpenModifierPopupForLine.bind(this);
    },

    async _mcdOpenModifierPopupForLine(line) {
        if (!line || !line.product) return;
        if (!mcdLineCanCustomize(this.pos, line)) return;

        const key = mcdProductTemplateId(line.product);
        const modifiersByTmpl = this.pos.mcd_modifiers_by_tmpl || {};
        const ingredients = modifiersByTmpl[key] || [];

        const { confirmed, payload } = await this.popup.add(ModifierPopup, {
            title: _t("Tùy chỉnh món"),
            productName: line.product.display_name,
            basePrice: line.get_unit_price(),
            ingredients,
        });

        if (confirmed && payload) {
            line.setModifierData(payload);
        }
    },

    _mcdOrderlineCanCustomize(line) {
        return mcdLineCanCustomize(this.pos, line);
    },

    async _mcdOpenModifierFromButton() {
        const order = this.pos.get_order();
        if (!order) {
            return;
        }
        const selectedLine = order.get_selected_orderline();
        let line = mcdLineCanCustomize(this.pos, selectedLine) ? selectedLine : null;
        if (!line) {
            const lines = [...order.get_orderlines()].reverse();
            line = lines.find((orderline) => mcdLineCanCustomize(this.pos, orderline));
        }
        if (!line) {
            await this.popup.add(ErrorPopup, {
                title: _t("Không có món để tùy chỉnh"),
                body: _t("Chỉ các món burger có nguyên liệu nhân bánh mới hỗ trợ tùy chỉnh."),
            });
            return;
        }
        await this._mcdOpenModifierPopupForLine(line);
    },
});

ProductScreen.addControlButton({
    component: CustomizeButton,
    condition() {
        return true;
    },
});

patch(Order.prototype, {
    setup() {
        super.setup(...arguments);
        this.mcd_service_type = this.mcd_service_type || "eat_in";
    },

    setMcdServiceType(serviceType) {
        this.assert_editable();
        this.mcd_service_type = serviceType === "take_out" ? "take_out" : "eat_in";
        this.save_to_db();
    },

    getMcdServiceType() {
        return this.mcd_service_type || "eat_in";
    },

    async add_product(product, options) {
        if (product?.mcd_pos_unavailable) {
            await this.pos.env.services.popup.add(ErrorPopup, {
                title: _t("Món đang tạm dừng phục vụ"),
                body: product.mcd_pos_unavailable_reason || _t("Nguyên liệu của món này hiện không đủ."),
            });
            return;
        }
        return await super.add_product(product, options);
    },

    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        json.mcd_service_type = this.getMcdServiceType();
        return json;
    },

    init_from_JSON(json) {
        super.init_from_JSON(...arguments);
        this.mcd_service_type = json.mcd_service_type || "eat_in";
    },
});

patch(PaymentScreen.prototype, {
    mcdSetServiceType(serviceType) {
        this.currentOrder.setMcdServiceType(serviceType);
    },

    mcdIsServiceType(serviceType) {
        return this.currentOrder.getMcdServiceType() === serviceType;
    },

});

patch(Orderline.prototype, {
    setup() {
        super.setup(...arguments);
        this.modifier_json = this.modifier_json || "";
        this.modifier_note = this.modifier_note || "";
        this.modifier_price_extra = Number(this.modifier_price_extra || 0);
        this.modifier_is_only = false;
        this._mcd_base_price = null;
    },

    setModifierData(data) {
        this.modifier_json = data.modifier_json || "";
        this.modifier_note = data.modifier_note || "";
        this.modifier_price_extra = Number(data.modifier_price_extra || 0);
        this.modifier_is_only = data.modifier_is_only || false;

        // Lưu base price 1 lần
        if (this._mcd_base_price === null || this._mcd_base_price === undefined) {
            this._mcd_base_price = this.get_unit_price();
        }

        this.price_type = "manual";

        if (this.modifier_is_only) {
            // Only mode: GIỮ NGUYÊN giá gốc, không cộng gì
            this.set_unit_price(this._mcd_base_price);
        } else {
            // Normal: cộng phí thêm vào giá gốc
            this.set_unit_price(this._mcd_base_price + this.modifier_price_extra);
        }

        // Hiển thị ghi chú trên order line
        this.set_customer_note(this.modifier_note);
    },

    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        json.modifier_json = this.modifier_json || "";
        json.modifier_note = this.modifier_note || "";
        json.modifier_price_extra = Number(this.modifier_price_extra || 0);
        json.modifier_is_only = this.modifier_is_only || false;
        return json;
    },

    init_from_JSON(json) {
        super.init_from_JSON(...arguments);
        this.modifier_json = json.modifier_json || "";
        this.modifier_note = json.modifier_note || "";
        this.modifier_price_extra = Number(json.modifier_price_extra || 0);
        this.modifier_is_only = json.modifier_is_only || false;
        this._mcd_base_price = this.get_unit_price() - (this.modifier_is_only ? 0 : this.modifier_price_extra);
    },
});
