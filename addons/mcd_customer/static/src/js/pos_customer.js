/** @odoo-module **/

import { onWillUnmount, useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.mcdCustomerState = useState({
            phone: "",
            name: "",
            birthdate: "",
            customer: null,
            isNew: false,
            lookupDone: false,
            loading: false,
            error: "",
            message: "",
        });
        this._mcdCustomerLookupTimer = null;
        onWillUnmount(() => {
            if (this._mcdCustomerLookupTimer) {
                clearTimeout(this._mcdCustomerLookupTimer);
            }
        });
    },

    _mcdPhoneDigits(value) {
        return (value || "").replace(/\D/g, "");
    },

    _mcdResetLookupState(keepPhone = true) {
        const phone = this.mcdCustomerState.phone;
        Object.assign(this.mcdCustomerState, {
            phone: keepPhone ? phone : "",
            name: "",
            birthdate: "",
            customer: null,
            isNew: false,
            lookupDone: false,
            loading: false,
            error: "",
            message: "",
        });
    },

    mcdPointPreview() {
        const total = this.currentOrder?.get_total_with_tax?.() || 0;
        return Math.floor(Math.max(total, 0) / 1000);
    },

    mcdOnPhoneInput(value) {
        if (this.currentOrder.get_partner()) {
            this.currentOrder.set_partner(null);
        }
        this.mcdCustomerState.phone = value || "";
        this.mcdCustomerState.error = "";
        this.mcdCustomerState.message = "";
        this.mcdCustomerState.customer = null;
        this.mcdCustomerState.lookupDone = false;
        this.mcdCustomerState.isNew = false;

        if (this._mcdCustomerLookupTimer) {
            clearTimeout(this._mcdCustomerLookupTimer);
        }

        const digits = this._mcdPhoneDigits(value);
        if (digits.length < 8) {
            return;
        }

        this._mcdCustomerLookupTimer = setTimeout(() => {
            this.mcdLookupCustomer(false);
        }, 450);
    },

    mcdOnNameInput(value) {
        this.mcdCustomerState.name = value || "";
        this.mcdCustomerState.error = "";
    },

    mcdOnBirthdateInput(value) {
        this.mcdCustomerState.birthdate = value || "";
    },

    async _mcdAttachCustomer(customer) {
        if (!customer?.id) {
            return false;
        }
        await this.pos._loadPartners([customer.id]);
        const partner = this.pos.db.get_partner_by_id(customer.id);
        if (!partner) {
            this.mcdCustomerState.error = "Không tải được thông tin khách hàng vào POS.";
            return false;
        }

        this.currentOrder.set_partner(partner);
        Object.assign(this.mcdCustomerState, {
            phone: customer.phone || this.mcdCustomerState.phone,
            name: customer.name || "",
            birthdate: customer.birthdate || "",
            customer,
            isNew: false,
            lookupDone: true,
            loading: false,
            error: "",
            message: "Đã gắn khách hàng vào đơn.",
        });
        return true;
    },

    async mcdLookupCustomer(showEmptyMessage = true) {
        const phone = this._mcdPhoneDigits(this.mcdCustomerState.phone);
        if (!phone) {
            this.mcdCustomerState.error = "Nhập số điện thoại để tìm khách hàng.";
            return false;
        }

        this.mcdCustomerState.loading = true;
        this.mcdCustomerState.error = "";
        this.mcdCustomerState.message = "";
        try {
            const customer = await this.orm.call("res.partner", "mcd_lookup_by_phone", [phone], {});
            if (customer) {
                return await this._mcdAttachCustomer(customer);
            }
            Object.assign(this.mcdCustomerState, {
                customer: null,
                isNew: true,
                lookupDone: true,
                loading: false,
                message: showEmptyMessage
                    ? "Số điện thoại này chưa có. Nhập tên và ngày sinh nếu khách muốn tích điểm."
                    : "",
            });
            return false;
        } catch (error) {
            console.warn("[MCD Customer] Lookup failed:", error);
            Object.assign(this.mcdCustomerState, {
                loading: false,
                error: "Không kiểm tra được khách hàng. Có thể tiếp tục thanh toán bình thường.",
            });
            return false;
        }
    },

    async mcdSaveCustomer() {
        const phone = this._mcdPhoneDigits(this.mcdCustomerState.phone);
        const name = (this.mcdCustomerState.name || "").trim();
        if (!phone) {
            this.mcdCustomerState.error = "Nhập số điện thoại trước khi lưu khách hàng.";
            return false;
        }
        if (!name) {
            this.mcdCustomerState.error = "Nhập tên khách hàng để tạo hồ sơ tích điểm.";
            return false;
        }

        this.mcdCustomerState.loading = true;
        this.mcdCustomerState.error = "";
        try {
            const customer = await this.orm.call(
                "res.partner",
                "mcd_find_or_create",
                [phone, name, this.mcdCustomerState.birthdate || false],
                {}
            );
            if (customer) {
                return await this._mcdAttachCustomer(customer);
            }
            this.mcdCustomerState.error = "Không lưu được khách hàng.";
            return false;
        } catch (error) {
            console.warn("[MCD Customer] Save failed:", error);
            Object.assign(this.mcdCustomerState, {
                loading: false,
                error: "Không lưu được khách hàng. Có thể tiếp tục thanh toán bình thường.",
            });
            return false;
        }
    },

    mcdClearCustomer() {
        if (this.currentOrder.get_partner()) {
            this.currentOrder.set_partner(null);
        }
        this._mcdResetLookupState(false);
    },

    async validateOrder(isForceValidate) {
        if (!this.currentOrder.get_partner()) {
            const phone = this._mcdPhoneDigits(this.mcdCustomerState.phone);
            const name = (this.mcdCustomerState.name || "").trim();
            if (phone && name) {
                await this.mcdSaveCustomer();
            } else if (phone && !this.mcdCustomerState.isNew) {
                await this.mcdLookupCustomer(false);
            }
        }
        return super.validateOrder(isForceValidate);
    },
});
