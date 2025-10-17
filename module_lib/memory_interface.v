module memory_interface (
    input [31:0] address,
    input [31:0] write_data,
    input mem_read,
    input mem_write,
    output reg [31:0] read_data
);

    // Memory array declaration
    reg [31:0] memory_array [0:1023]; // Example size, adjust as needed

    always @(*) begin
        if (mem_read) begin
            read_data = memory_array[address >> 2]; // Corrected for word-aligned access
        end
        if (mem_write) begin
            memory_array[address >> 2] = write_data;
        end
    end

endmodule
